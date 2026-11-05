import Phaser from 'phaser';
import type { AgentState, ClockState, WorldData } from '../types';

const SPRITE_SHEET = 'villagers';
const TILESET = 'tiles';

interface Actor {
  container: Phaser.GameObjects.Container;
  body: Phaser.GameObjects.Image | Phaser.GameObjects.Arc;
  label: Phaser.GameObjects.Text;
  status: Phaser.GameObjects.Text;
  bubble: Phaser.GameObjects.Container;
  bubbleTimer?: Phaser.Time.TimerEvent;
  tween?: Phaser.Tweens.Tween;
  state: AgentState;
}

export class VillageScene extends Phaser.Scene {
  private world!: WorldData;
  private ts = 24;
  private actors = new Map<string, Actor>();
  private nightOverlay!: Phaser.GameObjects.Rectangle;
  private hasSprites = false;
  private hasTiles = false;
  private lastTickAt = 0;
  private tickMs = 2000;

  constructor() {
    super('village');
  }

  init(data: { world: WorldData }) {
    this.world = data.world;
    this.ts = data.world.tile_size;
  }

  preload() {
    // Optional CC0 art. Missing files are fine: we fall back to vector shapes.
    this.load.on(Phaser.Loader.Events.FILE_LOAD_ERROR, (file: Phaser.Loader.File) => {
      if (file.key === SPRITE_SHEET) this.hasSprites = false;
      if (file.key === TILESET) this.hasTiles = false;
    });
    this.hasSprites = true;
    this.hasTiles = true;
    this.load.spritesheet(SPRITE_SHEET, 'assets/characters.png', {
      frameWidth: 16,
      frameHeight: 16,
    });
    this.load.image(TILESET, 'assets/grass.png');
  }

  create() {
    const { width, height } = this.world;
    const w = width * this.ts;
    const h = height * this.ts;

    if (this.hasTiles && this.textures.exists(TILESET)) {
      this.add.tileSprite(0, 0, w, h, TILESET).setOrigin(0).setDepth(0);
    } else {
      this.add.rectangle(0, 0, w, h, 0x3f5e3a).setOrigin(0).setDepth(0);
      this.drawGrassNoise(w, h);
    }

    this.drawZones();
    this.drawBuildings();
    this.drawProps();
    this.drawLocations();

    this.nightOverlay = this.add
      .rectangle(0, 0, w, h, 0x0a1030, 0)
      .setOrigin(0)
      .setDepth(900);

    this.cameras.main.setBounds(0, 0, w, h);
    this.input.on('pointerdown', (_p: Phaser.Input.Pointer, objects: unknown[]) => {
      if (objects.length === 0) this.game.events.emit('select', null);
    });
  }

  // ------------------------------------------------------------- map drawing
  private drawGrassNoise(w: number, h: number) {
    const g = this.add.graphics().setDepth(1);
    const rng = new Phaser.Math.RandomDataGenerator(['hollowmere']);
    for (let i = 0; i < 900; i++) {
      g.fillStyle(rng.pick([0x47693f, 0x395334, 0x4e7044]), 0.5);
      g.fillRect(rng.between(0, w), rng.between(0, h), rng.between(2, 6), rng.between(2, 5));
    }
  }

  private drawZones() {
    for (const z of this.world.zones) {
      const g = this.add.graphics().setDepth(2);
      g.fillStyle(Phaser.Display.Color.HexStringToColor(z.color).color, 0.22);
      g.fillRect(z.x * this.ts, z.y * this.ts, z.w * this.ts, z.h * this.ts);
      g.lineStyle(2, Phaser.Display.Color.HexStringToColor(z.color).color, 0.5);
      g.strokeRect(z.x * this.ts, z.y * this.ts, z.w * this.ts, z.h * this.ts);
      this.areaLabel(z.name, z.x, z.y, z.w);
    }
  }

  private drawBuildings() {
    for (const b of this.world.buildings) {
      const colour = Phaser.Display.Color.HexStringToColor(b.color).color;
      const g = this.add.graphics().setDepth(3);
      // Floor
      g.fillStyle(0x2b2520, 0.85);
      g.fillRect(b.x * this.ts, b.y * this.ts, b.w * this.ts, b.h * this.ts);
      // Walls
      g.fillStyle(colour, 1);
      for (let x = b.x; x < b.x + b.w; x++) {
        for (let y = b.y; y < b.y + b.h; y++) {
          const onEdge = x === b.x || x === b.x + b.w - 1 || y === b.y || y === b.y + b.h - 1;
          const isDoor = x === b.door[0] && y === b.door[1];
          if (onEdge && !isDoor) g.fillRect(x * this.ts, y * this.ts, this.ts, this.ts);
        }
      }
      // Doorway
      g.fillStyle(0x1a1410, 1);
      g.fillRect(b.door[0] * this.ts, b.door[1] * this.ts, this.ts, this.ts);
      this.areaLabel(b.name, b.x, b.y, b.w, colour);
    }
  }

  private areaLabel(name: string, x: number, y: number, w: number, tint = 0xffffff) {
    this.add
      .text(x * this.ts + (w * this.ts) / 2, y * this.ts - 9, name, {
        fontFamily: 'monospace',
        fontSize: '12px',
        color: Phaser.Display.Color.IntegerToColor(tint).rgba,
      })
      .setOrigin(0.5)
      .setDepth(850)
      .setAlpha(0.85);
  }

  private drawProps() {
    const glyphs: Record<string, string> = {
      tree: '🌲',
      stall: '🏪',
      crop: '🌾',
      fountain: '⛲',
    };
    for (const p of this.world.props) {
      this.add
        .text(p.x * this.ts + this.ts / 2, p.y * this.ts + this.ts / 2, glyphs[p.kind] ?? '▪', {
          fontSize: `${this.ts - 4}px`,
        })
        .setOrigin(0.5)
        .setDepth(5);
    }
  }

  private drawLocations() {
    for (const loc of this.world.locations) {
      const cx = loc.x * this.ts + this.ts / 2;
      const cy = loc.y * this.ts + this.ts / 2;
      this.add.circle(cx, cy, 5, 0xffe9a8, 0.55).setDepth(6);
      const dot = this.add.circle(cx, cy, 10, 0xffffff, 0.001).setDepth(7).setInteractive();
      dot.on('pointerover', () => this.game.events.emit('hover-location', loc));
      dot.on('pointerout', () => this.game.events.emit('hover-location', null));
    }
  }

  // ---------------------------------------------------------------- actors
  applyTick(agents: AgentState[], clock: ClockState) {
    const now = performance.now();
    if (this.lastTickAt) {
      this.tickMs = Phaser.Math.Clamp(now - this.lastTickAt, 300, 8000);
    }
    this.lastTickAt = now;

    for (const state of agents) {
      const actor = this.actors.get(state.id) ?? this.spawn(state);
      actor.state = state;
      this.moveActor(actor, state);
      actor.status.setText(this.statusText(state));
      if (state.speech) this.say(state.id, state.speech);
    }

    this.nightOverlay.setFillStyle(0x0a1030, clock.night ? 0.45 : clock.hour < 8 || clock.hour > 19 ? 0.2 : 0);
  }

  private statusText(s: AgentState) {
    const icon: Record<string, string> = {
      work: '🔨', eat: '🍞', sleep: '💤', socialise: '💬', talk: '💬',
      relax: '🌿', shop: '🪙', wander: '🚶', idle: '…',
    };
    return `${icon[s.action] ?? '•'} ${s.action}`;
  }

  private spawn(state: AgentState): Actor {
    const colour = Phaser.Display.Color.HexStringToColor(state.color).color;
    const container = this.add.container(this.px(state.x), this.px(state.y)).setDepth(500);

    let body: Phaser.GameObjects.Image | Phaser.GameObjects.Arc;
    if (this.hasSprites && this.textures.exists(SPRITE_SHEET)) {
      const frame = Number(state.sprite.split('_')[1] ?? 0);
      body = this.add.image(0, 0, SPRITE_SHEET, frame).setScale(this.ts / 16).setTint(colour);
    } else {
      body = this.add.circle(0, 0, this.ts * 0.4, colour);
      (body as Phaser.GameObjects.Arc).setStrokeStyle(2, 0x1a1a1a, 0.8);
      container.add(
        this.add
          .text(0, 0, state.name[0], {
            fontFamily: 'monospace',
            fontSize: '13px',
            color: '#12120f',
            fontStyle: 'bold',
          })
          .setOrigin(0.5),
      );
    }
    container.addAt(body, 0);

    const label = this.add
      .text(0, -this.ts * 0.75, state.name, {
        fontFamily: 'monospace',
        fontSize: '11px',
        color: '#ffffff',
        backgroundColor: '#00000088',
        padding: { x: 3, y: 1 },
      })
      .setOrigin(0.5);

    const status = this.add
      .text(0, this.ts * 0.62, '', {
        fontFamily: 'monospace',
        fontSize: '10px',
        color: '#cfe6ff',
      })
      .setOrigin(0.5);

    const bubble = this.add.container(0, -this.ts * 1.4).setDepth(950).setVisible(false);
    container.add([label, status, bubble]);

    const hit = this.add
      .circle(0, 0, this.ts * 0.6, 0xffffff, 0.001)
      .setInteractive({ useHandCursor: true });
    hit.on('pointerdown', () => this.game.events.emit('select', state.id));
    container.add(hit);

    const actor: Actor = { container, body, label, status, bubble, state };
    this.actors.set(state.id, actor);
    return actor;
  }

  private moveActor(actor: Actor, state: AgentState) {
    const points = state.trail?.length ? state.trail : [[state.x, state.y] as [number, number]];
    actor.tween?.stop();
    const perStep = this.tickMs / points.length;
    const targets = points.map(([x, y]) => ({ x: this.px(x), y: this.px(y) }));

    let i = 0;
    const hop = () => {
      if (i >= targets.length) return;
      const to = targets[i++];
      actor.tween = this.tweens.add({
        targets: actor.container,
        x: to.x,
        y: to.y,
        duration: perStep * 0.95,
        ease: 'Linear',
        onComplete: hop,
      });
    };
    hop();
  }

  say(agentId: string, text: string) {
    const actor = this.actors.get(agentId);
    if (!actor) return;
    actor.bubble.removeAll(true);

    const label = this.add
      .text(0, 0, text, {
        fontFamily: 'ui-sans-serif, system-ui, sans-serif',
        fontSize: '12px',
        color: '#15150f',
        wordWrap: { width: 190 },
        align: 'center',
      })
      .setOrigin(0.5);
    const bg = this.add
      .rectangle(0, 0, label.width + 14, label.height + 10, 0xfdf6e3, 0.97)
      .setStrokeStyle(2, 0x2a2a20, 0.9);
    const tail = this.add.triangle(0, bg.height / 2 + 4, -5, -4, 5, -4, 0, 6, 0xfdf6e3);

    actor.bubble.add([bg, tail, label]);
    actor.bubble.setVisible(true);
    actor.bubbleTimer?.remove();
    actor.bubbleTimer = this.time.delayedCall(7000, () => actor.bubble.setVisible(false));
  }

  highlight(agentId: string | null) {
    for (const [id, actor] of this.actors) {
      actor.container.setScale(id === agentId ? 1.25 : 1);
      actor.label.setColor(id === agentId ? '#ffe9a8' : '#ffffff');
    }
  }

  private px(tile: number) {
    return tile * this.ts + this.ts / 2;
  }
}
