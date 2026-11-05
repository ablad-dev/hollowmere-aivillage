export interface Needs {
  hunger: number;
  energy: number;
  social: number;
  fun: number;
}

export interface AgentState {
  id: string;
  name: string;
  color: string;
  sprite: string;
  job: string | null;
  model: string;
  x: number;
  y: number;
  trail: [number, number][];
  action: string;
  thought: string;
  target: string | null;
  area: string;
  location: string;
  coins: number;
  speech: string;
  partner: string | null;
  needs: Needs;
}

export interface Building {
  name: string;
  x: number;
  y: number;
  w: number;
  h: number;
  door: [number, number];
  color: string;
}

export interface Zone {
  name: string;
  x: number;
  y: number;
  w: number;
  h: number;
  color: string;
}

export interface Prop {
  kind: string;
  x: number;
  y: number;
}

export interface WorldLocation {
  name: string;
  x: number;
  y: number;
  kinds: string[];
  desc: string;
  job: string | null;
  price: number;
  area: string;
}

export interface WorldData {
  name: string;
  width: number;
  height: number;
  tile_size: number;
  buildings: Building[];
  zones: Zone[];
  props: Prop[];
  locations: WorldLocation[];
  blocked: [number, number][];
}

export interface ClockState {
  label: string;
  day: number;
  hour: number;
  minute: number;
  night: boolean;
}

export interface FeedEntry {
  tick: number;
  label: string;
  kind: string;
  text: string;
}

export type ServerMessage =
  | { type: 'init'; world: WorldData; agents: AgentState[]; clock: ClockState; feed: FeedEntry[]; live: boolean }
  | { type: 'tick'; tick: number; clock: ClockState; paused: boolean; agents: AgentState[]; usage: Record<string, { calls: number; prompt_tokens: number; completion_tokens: number }>; live: boolean }
  | { type: 'speech'; agent_id: string; name: string; text: string; location: string }
  | { type: 'event'; text: string; targets: string[]; label: string }
  | ({ type: 'chronicle' } & FeedEntry);
