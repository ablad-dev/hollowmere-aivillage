import Phaser from 'phaser';
import { Net, api } from './net';
import { VillageScene } from './scenes/VillageScene';
import { Panel } from './ui/panel';
import type { ServerMessage, WorldData } from './types';

async function boot() {
  const initial = await api.world();
  const world = initial.world as WorldData;

  const scene = new VillageScene();
  const game = new Phaser.Game({
    type: Phaser.AUTO,
    parent: 'game',
    backgroundColor: '#1b2016',
    width: world.width * world.tile_size,
    height: world.height * world.tile_size,
    scale: { mode: Phaser.Scale.FIT, autoCenter: Phaser.Scale.CENTER_BOTH },
    pixelArt: true,
    scene: [scene],
  });
  game.scene.start('village', { world });

  const panel = new Panel((id) => scene.highlight(id));
  panel.setAgents(initial.agents);
  panel.seedFeed(initial.feed ?? []);

  game.events.on('select', (id: string | null) => panel.select(id));

  const clockEl = document.getElementById('clock')!;
  const modeEl = document.getElementById('mode')!;

  const net = new Net();
  net.on((msg: ServerMessage) => {
    switch (msg.type) {
      case 'init':
        panel.setAgents(msg.agents);
        panel.seedFeed(msg.feed);
        modeEl.textContent = msg.live ? 'Foundry live' : 'mock mode';
        modeEl.className = `pill ${msg.live ? 'ok' : 'warn'}`;
        break;
      case 'tick':
        scene.applyTick(msg.agents, msg.clock);
        panel.setAgents(msg.agents);
        clockEl.textContent = `${msg.clock.label}${msg.paused ? '  ⏸' : ''}`;
        break;
      case 'speech':
        scene.say(msg.agent_id, msg.text);
        break;
      case 'chronicle':
        panel.push(msg);
        break;
      case 'event':
        panel.push({ tick: 0, label: msg.label, kind: 'event', text: `☁ ${msg.text}` });
        break;
    }
  });
  net.connect();
}

void boot();
