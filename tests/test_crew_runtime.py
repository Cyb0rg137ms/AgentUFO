from __future__ import annotations
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from server.crew_runtime import CrewRunManager

class DummyManager:
    def __init__(self):
        self.agentufo_coordinator = SimpleNamespace(seats=[SimpleNamespace(seat_id=f"seat_{i}", assigned_provider=None, assigned_model=None) for i in range(1, 4)])
        self.seen_config = None
        self.release = threading.Event()
        self.workspace_mgr = SimpleNamespace(discard_staged=lambda *a, **k: None, release_all_for_seat=lambda *a, **k: None)
    def configure_agentufo(self, cfg):
        self.seen_config = cfg
        n = cfg.get("num_seats", 3)
        self.agentufo_coordinator.seats = [SimpleNamespace(seat_id=f"seat_{i}", assigned_provider=None, assigned_model=None) for i in range(1, n+1)]
        return {"status":"success"}
    def run_agentufo_mission(self, prompt, progress_callback=None):
        if progress_callback:
            progress_callback("planning task", 12, {"stage":"planning"})
        self.release.wait(2)
        return {"status":"success", "final_code":"def f(): return 1", "target_filepaths":["solution.py"], "seats":[{"tokens_consumed":12}], "review":{"status":"PASSED","final_test_output":"ok"}}
    def get_agentufo_cockpit(self):
        return {"seats":[]}
    def execute_seat_action(self, seat_id, action, context=""):
        return {"success":True}

class CrewRunManagerTests(unittest.TestCase):
    def manager(self):
        dummy=DummyManager(); runtime=CrewRunManager(dummy)
        temp=tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        runtime.root=Path(temp.name); runtime.root.mkdir(parents=True,exist_ok=True); runtime.state_path=runtime.root/'runs.json'
        return runtime,dummy
    def wait_terminal(self, runtime, run_id):
        deadline=time.time()+3
        while time.time()<deadline:
            run=runtime.get_run(run_id)
            if run and run['status'] in {'completed','failed','failed_review','stopped','budget_exceeded','interrupted'}: return run
            time.sleep(.02)
        self.fail('run did not reach a terminal state')
    def test_run_state_is_persisted_and_result_normalized(self):
        runtime,dummy=self.manager()
        result=runtime.create({'prompt':'Implement a tiny function','agents':3,'selected_models':[],'token_saving':False})
        self.assertEqual(result['status'],'accepted')
        dummy.release.set()
        run=self.wait_terminal(runtime,result['run']['id'])
        self.assertEqual(run['status'],'completed')
        self.assertEqual(run['result']['code'],'def f(): return 1')
        self.assertTrue(runtime.state_path.exists())
    def test_stop_does_not_claim_success_for_inflight_request(self):
        runtime,dummy=self.manager()
        result=runtime.create({'prompt':'Test stop control','agents':3,'token_saving':False})
        run_id=result['run']['id']
        time.sleep(.08)
        control=runtime.control({'run_id':run_id,'action':'stop'})
        self.assertEqual(control['status'],'ok')
        dummy.release.set()
        run=self.wait_terminal(runtime,run_id)
        self.assertEqual(run['status'],'stopped')
    def test_second_concurrent_run_is_rejected(self):
        runtime,dummy=self.manager()
        result=runtime.create({'prompt':'First task','agents':3,'token_saving':False})
        second=runtime.create({'prompt':'Second task','agents':3,'token_saving':False})
        self.assertEqual(result['status'],'accepted')
        self.assertEqual(second['status'],'error')
        dummy.release.set()
        self.wait_terminal(runtime,result['run']['id'])
    def test_empty_task_is_rejected(self):
        runtime,_=self.manager()
        self.assertEqual(runtime.create({'prompt':' '})['status'],'error')
    def test_unimplemented_modes_and_excess_models_fail_explicitly(self):
        runtime,_=self.manager()
        self.assertEqual(runtime.create({'prompt':'split it','mode':'split_work'})['status'],'error')
        too_many=[{'provider':'openai','model':f'model-{i}'} for i in range(4)]
        self.assertEqual(runtime.create({'prompt':'test routing','selected_models':too_many})['status'],'error')
    def test_invalid_budget_input_is_rejected_not_server_error(self):
        runtime,_=self.manager()
        self.assertEqual(runtime.create({'prompt':'test budget','budget_usd':'not-a-number'})['status'],'error')

if __name__=='__main__':
    unittest.main()
