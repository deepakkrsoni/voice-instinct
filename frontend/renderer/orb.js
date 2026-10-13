// Siri-like orb: a soft gradient blob whose size follows the audio level; colour follows the mode.
const COLOURS = {
  listening: ["#7c3aed", "#22d3ee"],
  thinking: ["#6366f1", "#a855f7"],
  speaking: ["#ec4899", "#8b5cf6"],
  hidden: ["#000000", "#000000"],
};

export class Orb {
  constructor(canvas) {
    this.canvas = canvas;
    this.ctx = canvas.getContext("2d");
    this.mode = "hidden";
    this.level = 0;
    this.target = 0;
    this.t = 0;
    requestAnimationFrame(() => this.frame());
  }

  setMode(mode) { this.mode = mode; }

  setLevel(level) { this.target = Math.max(0, Math.min(1, level)); }

  frame() {
    const c = this.canvas, g = this.ctx;
    const dpr = window.devicePixelRatio || 1;
    if (c.width !== c.clientWidth * dpr) { c.width = c.clientWidth * dpr; c.height = c.clientHeight * dpr; }
    const w = c.width, h = c.height, r0 = Math.min(w, h) * 0.28;
    this.t += 0.03;
    this.level += (this.target - this.level) * 0.2;
    g.clearRect(0, 0, w, h);
    if (this.mode !== "hidden") {
      const [a, b] = COLOURS[this.mode] || COLOURS.listening;
      const pulse = this.mode === "thinking" ? 0.06 * Math.sin(this.t * 3) : 0;
      for (let i = 0; i < 3; i++) {
        const r = r0 * (1 + 0.35 * this.level + pulse + 0.05 * Math.sin(this.t * (1.3 + i) + i));
        const ox = Math.cos(this.t + i * 2.1) * r0 * 0.12, oy = Math.sin(this.t * 1.1 + i * 2.1) * r0 * 0.12;
        const grad = g.createRadialGradient(w / 2 + ox, h / 2 + oy, r * 0.1, w / 2 + ox, h / 2 + oy, r);
        grad.addColorStop(0, i % 2 ? a : b);
        grad.addColorStop(1, "rgba(0,0,0,0)");
        g.globalAlpha = 0.55;
        g.fillStyle = grad;
        g.beginPath();
        g.arc(w / 2 + ox, h / 2 + oy, r, 0, Math.PI * 2);
        g.fill();
      }
      g.globalAlpha = 1;
    }
    requestAnimationFrame(() => this.frame());
  }
}
