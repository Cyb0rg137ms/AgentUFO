"""
starter_artifacts.py
=====================
Pre-seeded, high-fidelity interactive applications and algorithmic artifacts
for the Agent-IDE Sandbox. Clean, self-contained implementations without
relying on an LLM provider or external network requests.
"""

from __future__ import annotations
import time
from typing import Any, Dict, List


SNAKE_GAME_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Snake Arcade</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    background: radial-gradient(circle at 50% 50%, #0d1117, #010409);
    color: #e6edf3;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    min-height: 100vh;
    overflow: hidden;
  }
  .hud {
    display: flex;
    gap: 2rem;
    font-size: 1.1rem;
    font-weight: 600;
    margin-bottom: 1rem;
    letter-spacing: 0.05em;
  }
  .hud span { color: #58a6ff; }
  canvas {
    background: #161b22;
    border: 2px solid #30363d;
    border-radius: 8px;
    box-shadow: 0 8px 32px rgba(0,0,0,0.5), 0 0 16px rgba(88,166,255,0.2);
  }
  .controls {
    margin-top: 1rem;
    font-size: 0.85rem;
    color: #8b949e;
  }
</style>
</head>
<body>
  <div class="hud">
    <div>SCORE: <span id="score">0</span></div>
    <div>HIGH: <span id="high">0</span></div>
    <div>SPEED: <span id="speed">1x</span></div>
  </div>
  <canvas id="canvas" width="400" height="400"></canvas>
  <div class="controls">Arrow Keys or WASD to Move · Space to Restart</div>
<script>
  const canvas = document.getElementById('canvas');
  const ctx = canvas.getContext('2d');
  const scoreEl = document.getElementById('score');
  const highEl = document.getElementById('high');
  const speedEl = document.getElementById('speed');

  const GRID = 20;
  const COUNT = canvas.width / GRID;
  let snake = [{x: 10, y: 10}, {x: 9, y: 10}, {x: 8, y: 10}];
  let dx = 1, dy = 0;
  let food = {x: 15, y: 10};
  let score = 0;
  let high = 0;
  let gameOver = false;
  let lastTime = 0;
  let stepInterval = 110;

  function spawnFood() {
    let valid = false;
    while (!valid) {
      food.x = Math.floor(Math.random() * COUNT);
      food.y = Math.floor(Math.random() * COUNT);
      valid = !snake.some(s => s.x === food.x && s.y === food.y);
    }
  }

  function resetGame() {
    snake = [{x: 10, y: 10}, {x: 9, y: 10}, {x: 8, y: 10}];
    dx = 1; dy = 0;
    score = 0;
    gameOver = false;
    stepInterval = 110;
    scoreEl.textContent = score;
    speedEl.textContent = '1x';
    spawnFood();
  }

  window.addEventListener('keydown', e => {
    if (['ArrowUp', 'KeyW'].includes(e.code) && dy === 0) { dx = 0; dy = -1; }
    else if (['ArrowDown', 'KeyS'].includes(e.code) && dy === 0) { dx = 0; dy = 1; }
    else if (['ArrowLeft', 'KeyA'].includes(e.code) && dx === 0) { dx = -1; dy = 0; }
    else if (['ArrowRight', 'KeyD'].includes(e.code) && dx === 0) { dx = 1; dy = 0; }
    else if (e.code === 'Space' && gameOver) { resetGame(); }
  });

  function update() {
    if (gameOver) return;
    const head = { x: snake[0].x + dx, y: snake[0].y + dy };

    // Wall collision
    if (head.x < 0 || head.x >= COUNT || head.y < 0 || head.y >= COUNT) {
      gameOver = true;
      return;
    }
    // Self collision
    if (snake.some(s => s.x === head.x && s.y === head.y)) {
      gameOver = true;
      return;
    }

    snake.unshift(head);
    if (head.x === food.x && head.y === food.y) {
      score += 10;
      if (score > high) { high = score; highEl.textContent = high; }
      scoreEl.textContent = score;
      stepInterval = Math.max(50, 110 - Math.floor(score / 50) * 10);
      speedEl.textContent = (110 / stepInterval).toFixed(1) + 'x';
      spawnFood();
    } else {
      snake.pop();
    }
  }

  function draw() {
    ctx.fillStyle = '#0d1117';
    ctx.fillRect(0, 0, canvas.width, canvas.height);

    // Grid lines
    ctx.strokeStyle = '#161b22';
    for (let i = 0; i < canvas.width; i += GRID) {
      ctx.beginPath(); ctx.moveTo(i, 0); ctx.lineTo(i, canvas.height); ctx.stroke();
      ctx.beginPath(); ctx.moveTo(0, i); ctx.lineTo(canvas.width, i); ctx.stroke();
    }

    // Food
    ctx.fillStyle = '#f85149';
    ctx.shadowBlur = 10;
    ctx.shadowColor = '#f85149';
    ctx.beginPath();
    ctx.arc((food.x + 0.5) * GRID, (food.y + 0.5) * GRID, GRID * 0.4, 0, Math.PI * 2);
    ctx.fill();
    ctx.shadowBlur = 0;

    // Snake
    snake.forEach((part, idx) => {
      ctx.fillStyle = idx === 0 ? '#58a6ff' : '#238636';
      ctx.fillRect(part.x * GRID + 1, part.y * GRID + 1, GRID - 2, GRID - 2);
    });

    if (gameOver) {
      ctx.fillStyle = 'rgba(0, 0, 0, 0.75)';
      ctx.fillRect(0, 0, canvas.width, canvas.height);
      ctx.fillStyle = '#f85149';
      ctx.font = 'bold 24px sans-serif';
      ctx.textAlign = 'center';
      ctx.fillText('GAME OVER', canvas.width / 2, canvas.height / 2 - 10);
      ctx.fillStyle = '#8b949e';
      ctx.font = '14px sans-serif';
      ctx.fillText('Press SPACE to Restart', canvas.width / 2, canvas.height / 2 + 20);
    }
  }

  function loop(timestamp) {
    if (timestamp - lastTime > stepInterval) {
      update();
      lastTime = timestamp;
    }
    draw();
    requestAnimationFrame(loop);
  }

  spawnFood();
  requestAnimationFrame(loop);
</script>
</body>
</html>"""


BREAKOUT_GAME_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Breakout — Neon Brick Breaker</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    background: #090d16;
    color: #e6edf3;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    min-height: 100vh;
  }
  .hud {
    display: flex;
    gap: 3rem;
    font-weight: 700;
    margin-bottom: 12px;
    font-size: 1.1rem;
    letter-spacing: 0.05em;
  }
  .hud span { color: #bc8cff; }
  canvas {
    background: #0d1117;
    border: 2px solid #30363d;
    border-radius: 8px;
    box-shadow: 0 8px 32px rgba(0,0,0,0.7), 0 0 20px rgba(188,140,255,0.2);
  }
  .footer { margin-top: 10px; font-size: 0.85rem; color: #8b949e; }
</style>
</head>
<body>
  <div class="hud">
    <div>SCORE: <span id="score">0</span></div>
    <div>LIVES: <span id="lives">3</span></div>
  </div>
  <canvas id="canvas" width="480" height="360"></canvas>
  <div class="footer">Move mouse or arrow keys to control paddle</div>
<script>
  const canvas = document.getElementById('canvas');
  const ctx = canvas.getContext('2d');
  const scoreEl = document.getElementById('score');
  const livesEl = document.getElementById('lives');

  let score = 0;
  let lives = 3;
  let paddleWidth = 80, paddleHeight = 10;
  let paddleX = (canvas.width - paddleWidth) / 2;
  let rightPressed = false, leftPressed = false;

  let x = canvas.width / 2, y = canvas.height - 30;
  let dx = 3, dy = -3;
  let ballRadius = 6;

  const rowCount = 5, colCount = 8;
  const brickWidth = 50, brickHeight = 14, brickPadding = 6;
  const brickOffsetTop = 30, brickOffsetLeft = 20;
  const colors = ['#f85149', '#ff7b72', '#d29922', '#3fb950', '#58a6ff'];

  let bricks = [];
  function initBricks() {
    bricks = [];
    for (let c = 0; c < colCount; c++) {
      bricks[c] = [];
      for (let r = 0; r < rowCount; r++) {
        bricks[c][r] = { x: 0, y: 0, status: 1, color: colors[r % colors.length] };
      }
    }
  }
  initBricks();

  document.addEventListener('keydown', e => {
    if (e.key === 'Right' || e.key === 'ArrowRight') rightPressed = true;
    else if (e.key === 'Left' || e.key === 'ArrowLeft') leftPressed = true;
  });
  document.addEventListener('keyup', e => {
    if (e.key === 'Right' || e.key === 'ArrowRight') rightPressed = false;
    else if (e.key === 'Left' || e.key === 'ArrowLeft') leftPressed = false;
  });
  document.addEventListener('mousemove', e => {
    const rect = canvas.getBoundingClientRect();
    const relativeX = e.clientX - rect.left;
    if (relativeX > 0 && relativeX < canvas.width) {
      paddleX = Math.max(0, Math.min(canvas.width - paddleWidth, relativeX - paddleWidth / 2));
    }
  });

  function collisionDetection() {
    for (let c = 0; c < colCount; c++) {
      for (let r = 0; r < rowCount; r++) {
        const b = bricks[c][r];
        if (b.status === 1) {
          if (x > b.x && x < b.x + brickWidth && y > b.y && y < b.y + brickHeight) {
            dy = -dy;
            b.status = 0;
            score += 10;
            scoreEl.textContent = score;
            if (score === rowCount * colCount * 10) {
              alert('CONGRATULATIONS! YOU WIN!');
              document.location.reload();
            }
          }
        }
      }
    }
  }

  function drawBall() {
    ctx.beginPath();
    ctx.arc(x, y, ballRadius, 0, Math.PI * 2);
    ctx.fillStyle = '#58a6ff';
    ctx.shadowBlur = 8;
    ctx.shadowColor = '#58a6ff';
    ctx.fill();
    ctx.closePath();
    ctx.shadowBlur = 0;
  }

  function drawPaddle() {
    ctx.beginPath();
    ctx.rect(paddleX, canvas.height - paddleHeight - 8, paddleWidth, paddleHeight);
    ctx.fillStyle = '#bc8cff';
    ctx.shadowBlur = 10;
    ctx.shadowColor = '#bc8cff';
    ctx.fill();
    ctx.closePath();
    ctx.shadowBlur = 0;
  }

  function drawBricks() {
    for (let c = 0; c < colCount; c++) {
      for (let r = 0; r < rowCount; r++) {
        if (bricks[c][r].status === 1) {
          const brickX = c * (brickWidth + brickPadding) + brickOffsetLeft;
          const brickY = r * (brickHeight + brickPadding) + brickOffsetTop;
          bricks[c][r].x = brickX;
          bricks[c][r].y = brickY;
          ctx.beginPath();
          ctx.rect(brickX, brickY, brickWidth, brickHeight);
          ctx.fillStyle = bricks[c][r].color;
          ctx.fill();
          ctx.closePath();
        }
      }
    }
  }

  function draw() {
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    drawBricks();
    drawBall();
    drawPaddle();
    collisionDetection();

    if (x + dx > canvas.width - ballRadius || x + dx < ballRadius) dx = -dx;
    if (y + dy < ballRadius) dy = -dy;
    else if (y + dy > canvas.height - paddleHeight - 8 - ballRadius) {
      if (x > paddleX && x < paddleX + paddleWidth) {
        dy = -Math.abs(dy);
        dx = 6 * ((x - (paddleX + paddleWidth / 2)) / paddleWidth);
      } else if (y + dy > canvas.height - ballRadius) {
        lives--;
        livesEl.textContent = lives;
        if (!lives) {
          alert('GAME OVER');
          document.location.reload();
          return;
        } else {
          x = canvas.width / 2;
          y = canvas.height - 30;
          dx = 3;
          dy = -3;
          paddleX = (canvas.width - paddleWidth) / 2;
        }
      }
    }

    if (rightPressed && paddleX < canvas.width - paddleWidth) paddleX += 5;
    else if (leftPressed && paddleX > 0) paddleX -= 5;

    x += dx;
    y += dy;
    requestAnimationFrame(draw);
  }
  draw();
</script>
</body>
</html>"""


CALCULATOR_APP_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Scientific Calculator App</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    background: #0d1117;
    color: #e6edf3;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    display: flex;
    align-items: center;
    justify-content: center;
    min-height: 100vh;
  }
  .calculator {
    background: #161b22;
    border: 1px solid #30363d;
    border-radius: 12px;
    padding: 20px;
    width: 320px;
    box-shadow: 0 16px 40px rgba(0,0,0,0.6);
  }
  .display {
    background: #090d16;
    border: 1px solid #30363d;
    border-radius: 8px;
    padding: 12px 16px;
    text-align: right;
    font-size: 1.8rem;
    font-family: 'Courier New', monospace;
    color: #58a6ff;
    min-height: 56px;
    margin-bottom: 16px;
    word-break: break-all;
  }
  .keys {
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 8px;
  }
  button {
    background: #21262d;
    color: #e6edf3;
    border: 1px solid #30363d;
    border-radius: 6px;
    font-size: 1.1rem;
    font-weight: 600;
    padding: 12px 0;
    cursor: pointer;
    transition: all 0.15s ease;
  }
  button:hover { background: #30363d; border-color: #58a6ff; color: #58a6ff; }
  button:active { transform: scale(0.96); }
  button.operator { color: #f0883e; }
  button.equals { background: #238636; border-color: #2ea043; color: #fff; grid-column: span 2; }
  button.equals:hover { background: #2ea043; }
  button.clear { color: #f85149; }
</style>
</head>
<body>
  <div class="calculator">
    <div class="display" id="display">0</div>
    <div class="keys">
      <button class="clear" onclick="clearDisplay()">C</button>
      <button class="operator" onclick="appendOp('(')">(</button>
      <button class="operator" onclick="appendOp(')')">)</button>
      <button class="operator" onclick="appendOp('/')">/</button>

      <button onclick="appendNum('7')">7</button>
      <button onclick="appendNum('8')">8</button>
      <button onclick="appendNum('9')">9</button>
      <button class="operator" onclick="appendOp('*')">×</button>

      <button onclick="appendNum('4')">4</button>
      <button onclick="appendNum('5')">5</button>
      <button onclick="appendNum('6')">6</button>
      <button class="operator" onclick="appendOp('-')">-</button>

      <button onclick="appendNum('1')">1</button>
      <button onclick="appendNum('2')">2</button>
      <button onclick="appendNum('3')">3</button>
      <button class="operator" onclick="appendOp('+')">+</button>

      <button onclick="appendNum('0')">0</button>
      <button onclick="appendNum('.')">.</button>
      <button class="equals" onclick="compute()">=</button>
    </div>
  </div>
<script>
  let currentExpr = '0';
  const display = document.getElementById('display');

  function update() { display.textContent = currentExpr; }
  function clearDisplay() { currentExpr = '0'; update(); }
  function appendNum(n) {
    if (currentExpr === '0' && n !== '.') currentExpr = n;
    else currentExpr += n;
    update();
  }
  function appendOp(op) {
    if (currentExpr === '0' && op === '-') currentExpr = '-';
    else currentExpr += op;
    update();
  }
  function compute() {
    try {
      const sanitized = currentExpr.replace(/×/g, '*');
      const res = Function('"use strict";return (' + sanitized + ')')();
      currentExpr = String(Math.round(res * 1e8) / 1e8);
    } catch (e) {
      currentExpr = 'Error';
    }
    update();
  }
</script>
</body>
</html>"""


TEMPLE_RUN_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Temple Run — 3D Runway Runner</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    background: #010409;
    color: #e6edf3;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    min-height: 100vh;
    overflow: hidden;
  }
  .hud {
    display: flex;
    gap: 3rem;
    font-size: 1.1rem;
    font-weight: 700;
    margin-bottom: 12px;
  }
  .hud span { color: #f0883e; }
  canvas {
    background: #0d1117;
    border: 2px solid #30363d;
    border-radius: 8px;
    box-shadow: 0 10px 40px rgba(0,0,0,0.8), 0 0 20px rgba(240,136,62,0.3);
  }
  .hint { margin-top: 10px; font-size: 0.85rem; color: #8b949e; }
</style>
</head>
<body>
  <div class="hud">
    <div>DISTANCE: <span id="dist">0</span>m</div>
    <div>COINS: <span id="coins">0</span></div>
  </div>
  <canvas id="canvas" width="480" height="420"></canvas>
  <div class="hint">Use LEFT / RIGHT arrows or A / D to switch lanes · UP to jump</div>
<script>
  const canvas = document.getElementById('canvas');
  const ctx = canvas.getContext('2d');
  const distEl = document.getElementById('dist');
  const coinsEl = document.getElementById('coins');

  let lane = 1; // 0=left, 1=center, 2=right
  let jumpY = 0;
  let isJumping = false;
  let distance = 0;
  let coins = 0;
  let gameOver = false;

  let obstacles = [];
  let pickups = [];

  const laneX = [140, 240, 340];

  window.addEventListener('keydown', e => {
    if (['ArrowLeft', 'KeyA'].includes(e.code) && lane > 0) lane--;
    else if (['ArrowRight', 'KeyD'].includes(e.code) && lane < 2) lane++;
    else if (['ArrowUp', 'KeyW', 'Space'].includes(e.code) && !isJumping) {
      isJumping = true;
      let jumpSpeed = 8;
      const jumpInterval = setInterval(() => {
        jumpY += jumpSpeed;
        jumpSpeed -= 0.6;
        if (jumpY <= 0) {
          jumpY = 0;
          isJumping = false;
          clearInterval(jumpInterval);
        }
      }, 16);
    }
  });

  function spawnEntities() {
    if (Math.random() < 0.03) {
      obstacles.push({ lane: Math.floor(Math.random() * 3), y: -20 });
    }
    if (Math.random() < 0.04) {
      pickups.push({ lane: Math.floor(Math.random() * 3), y: -20 });
    }
  }

  function update() {
    if (gameOver) return;
    distance += 1;
    distEl.textContent = distance;
    spawnEntities();

    obstacles.forEach((obs, idx) => {
      obs.y += 4;
      if (obs.y > 340 && obs.y < 380 && obs.lane === lane && jumpY < 20) {
        gameOver = true;
      }
    });
    obstacles = obstacles.filter(o => o.y < canvas.height);

    pickups.forEach((p, idx) => {
      p.y += 4;
      if (p.y > 340 && p.y < 380 && p.lane === lane) {
        coins += 1;
        coinsEl.textContent = coins;
        p.collected = true;
      }
    });
    pickups = pickups.filter(p => !p.collected && p.y < canvas.height);
  }

  function draw() {
    ctx.clearRect(0, 0, canvas.width, canvas.height);

    // Perspective roadway
    ctx.fillStyle = '#1c2128';
    ctx.beginPath();
    ctx.moveTo(180, 0);
    ctx.lineTo(300, 0);
    ctx.lineTo(440, canvas.height);
    ctx.lineTo(40, canvas.height);
    ctx.fill();

    // Lanes
    ctx.strokeStyle = '#30363d';
    ctx.setLineDash([15, 15]);
    ctx.beginPath();
    ctx.moveTo(220, 0); ctx.lineTo(173, canvas.height);
    ctx.moveTo(260, 0); ctx.lineTo(306, canvas.height);
    ctx.stroke();
    ctx.setLineDash([]);

    // Obstacles
    obstacles.forEach(o => {
      ctx.fillStyle = '#f85149';
      ctx.fillRect(laneX[o.lane] - 20, o.y, 40, 20);
    });

    // Pickups
    pickups.forEach(p => {
      ctx.fillStyle = '#f0883e';
      ctx.beginPath();
      ctx.arc(laneX[p.lane], p.y + 10, 8, 0, Math.PI * 2);
      ctx.fill();
    });

    // Player
    ctx.fillStyle = '#58a6ff';
    ctx.fillRect(laneX[lane] - 15, 360 - jumpY, 30, 30);

    if (gameOver) {
      ctx.fillStyle = 'rgba(0,0,0,0.7)';
      ctx.fillRect(0, 0, canvas.width, canvas.height);
      ctx.fillStyle = '#f85149';
      ctx.font = 'bold 24px sans-serif';
      ctx.textAlign = 'center';
      ctx.fillText('CRASHED! GAME OVER', canvas.width / 2, canvas.height / 2);
    }
  }

  function loop() {
    update();
    draw();
    requestAnimationFrame(loop);
  }
  requestAnimationFrame(loop);
</script>
</body>
</html>"""


SAFE_DIVISION_PYTHON = '''def divide(a: float, b: float) -> float:
    """Divide safely handling zero divisor invariant."""
    if b == 0:
        return 0.0
    return a / b
'''


def get_starter_artifacts() -> List[Dict[str, Any]]:
    """Returns the set of verified starter artifacts for the sandbox workspace."""
    now = time.time()
    return [
        {
            "id": "art-snake-game",
            "title": "Snake Arcade — HTML5 Canvas Game",
            "type": "html_app",
            "language": "html",
            "code": SNAKE_GAME_HTML,
            "created_at": now - 3600,
            "status": "verified",
        },
        {
            "id": "art-breakout",
            "title": "Breakout — Neon Brick Breaker",
            "type": "html_app",
            "language": "html",
            "code": BREAKOUT_GAME_HTML,
            "created_at": now - 2400,
            "status": "verified",
        },
        {
            "id": "art-calculator",
            "title": "Scientific Calculator App",
            "type": "html_app",
            "language": "html",
            "code": CALCULATOR_APP_HTML,
            "created_at": now - 1800,
            "status": "verified",
        },
        {
            "id": "art-safe-division",
            "title": "Safe Division Invariant Guard",
            "type": "python_script",
            "language": "python",
            "code": SAFE_DIVISION_PYTHON,
            "created_at": now - 600,
            "status": "passed_tests",
        },
        {
            "id": "art-temple-run",
            "title": "Temple Run — 3D Runway Runner",
            "type": "html_app",
            "language": "html",
            "code": TEMPLE_RUN_HTML,
            "created_at": now - 300,
            "status": "verified",
        },
    ]
