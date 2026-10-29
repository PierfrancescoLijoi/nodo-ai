const OPEN = 1;
const MAX_BUFFERED_AUDIO_MS = 60_000;

export interface VoiceSession {
  sessionId: string;
  ticket: string;
  protocolVersion: number;
  streamUrl: string;
  heartbeatSeconds: number;
  reconnectGraceSeconds: number;
}

export interface VoiceEvent {
  type: string;
  sequence?: number;
  audio_resume_offset_ms?: number;
  [field: string]: unknown;
}

export interface VoiceSocketEvent {
  data: string | ArrayBuffer | Blob;
}

export interface SocketLike {
  binaryType: BinaryType;
  readyState: number;
  onopen: (() => void) | null;
  onmessage: ((event: VoiceSocketEvent) => void) | null;
  onclose: ((event: { code: number }) => void) | null;
  onerror: (() => void) | null;
  send(data: string | ArrayBuffer): void;
  close(code?: number): void;
}

interface BufferedFrame {
  offsetMs: number;
  endMs: number;
  data: ArrayBuffer;
}

interface Dependencies {
  createSocket?: (url: string) => SocketLike;
  refreshTicket(sessionId: string): Promise<string>;
  schedule?: (callback: () => void, delayMs: number) => number;
  cancelSchedule?: (id: number) => void;
  startHeartbeat?: (callback: () => void, intervalMs: number) => number;
  stopHeartbeat?: (id: number) => void;
  now?: () => number;
  onStatus?: (status: 'connecting' | 'connected' | 'reconnecting' | 'closed') => void;
  onError?: (error: Error) => void;
}

function encodeFrame(offsetMs: number, pcm: Int16Array): ArrayBuffer {
  const frame = new ArrayBuffer(4 + pcm.byteLength);
  const view = new DataView(frame);
  view.setUint32(0, offsetMs, false);
  for (let index = 0; index < pcm.length; index += 1) {
    view.setInt16(4 + index * 2, pcm[index] ?? 0, true);
  }
  return frame;
}

export class VoiceSessionClient {
  private socket?: SocketLike;
  private frames: BufferedFrame[] = [];
  private lastAck = 0;
  private ready = false;
  private stopped = false;
  private reconnectTimer?: number;
  private heartbeatTimer?: number;
  private disconnectedAt?: number;
  private ticket: string;

  private readonly createSocket: (url: string) => SocketLike;
  private readonly schedule: (callback: () => void, delayMs: number) => number;
  private readonly cancelSchedule: (id: number) => void;
  private readonly startHeartbeat: (callback: () => void, intervalMs: number) => number;
  private readonly stopHeartbeat: (id: number) => void;
  private readonly now: () => number;

  constructor(
    private readonly session: VoiceSession,
    private readonly onEvent: (event: VoiceEvent) => void,
    private readonly deps: Dependencies,
  ) {
    this.ticket = session.ticket;
    this.createSocket = deps.createSocket ?? ((url) => new WebSocket(url) as unknown as SocketLike);
    this.schedule = deps.schedule ?? ((callback, delay) => window.setTimeout(callback, delay));
    this.cancelSchedule = deps.cancelSchedule ?? ((id) => window.clearTimeout(id));
    this.startHeartbeat =
      deps.startHeartbeat ?? ((callback, interval) => window.setInterval(callback, interval));
    this.stopHeartbeat = deps.stopHeartbeat ?? ((id) => window.clearInterval(id));
    this.now = deps.now ?? Date.now;
  }

  connect() {
    if (this.stopped || this.socket) return;
    this.deps.onStatus?.(this.disconnectedAt ? 'reconnecting' : 'connecting');
    const socket = this.createSocket(this.session.streamUrl);
    socket.binaryType = 'arraybuffer';
    socket.onopen = () => this.handshake(socket);
    socket.onmessage = (event) => this.receive(socket, event);
    socket.onclose = () => this.disconnected(socket);
    socket.onerror = () => undefined; // onclose owns retry; browsers otherwise report both.
    this.socket = socket;
  }

  sendPcm(offsetMs: number, pcm: Int16Array) {
    if (this.stopped) return;
    const durationMs = (pcm.length / 16_000) * 1_000;
    const buffered = this.frames.at(-1)?.endMs ?? offsetMs;
    if (buffered - (this.frames[0]?.offsetMs ?? offsetMs) >= MAX_BUFFERED_AUDIO_MS) {
      this.fail(new Error('Voice reconnect buffer is full'));
      return;
    }
    const frame = { offsetMs, endMs: offsetMs + durationMs, data: encodeFrame(offsetMs, pcm) };
    this.frames.push(frame);
    if (this.ready && this.socket?.readyState === OPEN) this.socket.send(frame.data);
  }

  end() {
    if (this.stopped) return;
    this.stopped = true;
    this.clearTimers();
    this.sendControl({ type: 'end' });
    this.deps.onStatus?.('closed');
  }

  dispose() {
    this.stopped = true;
    this.clearTimers();
    this.socket?.close(1000);
    this.socket = undefined;
    this.frames = [];
  }

  private handshake(socket: SocketLike) {
    if (socket !== this.socket || this.stopped) return;
    socket.send(
      JSON.stringify({
        type: 'hello',
        protocol_version: this.session.protocolVersion,
        ticket: this.ticket,
        last_ack: this.lastAck,
      }),
    );
  }

  private receive(socket: SocketLike, message: VoiceSocketEvent) {
    if (socket !== this.socket || typeof message.data !== 'string') return;
    try {
      const event = JSON.parse(message.data) as VoiceEvent;
      if (typeof event.type !== 'string') throw new Error('Invalid voice event');
      if (event.type === 'session.ready') {
        const offset = event.audio_resume_offset_ms ?? 0;
        if (typeof offset !== 'number' || !Number.isSafeInteger(offset) || offset < 0) {
          throw new Error('Invalid audio offset');
        }
        this.resume(offset);
        return;
      }
      const sequence = typeof event.sequence === 'number' ? event.sequence : undefined;
      if (sequence !== undefined && (!Number.isSafeInteger(sequence) || sequence < 0)) {
        throw new Error('Invalid voice event sequence');
      }
      if (sequence !== undefined && sequence <= this.lastAck) return;
      if (event.type === 'transcript.final' && event.audio_offset_end_ms !== undefined) {
        if (
          typeof event.audio_offset_end_ms !== 'number' ||
          !Number.isSafeInteger(event.audio_offset_end_ms) ||
          event.audio_offset_end_ms < 0
        ) {
          throw new Error('Invalid audio offset');
        }
        this.dropDurableAudio(event.audio_offset_end_ms);
      }
      this.onEvent(event);
      if (sequence !== undefined) {
        this.lastAck = sequence;
        this.sendControl({ type: 'ack', sequence });
      }
      if (event.type === 'session.closed') {
        this.stopped = true;
        this.clearTimers();
        this.deps.onStatus?.('closed');
      }
    } catch (error) {
      this.fail(error instanceof Error ? error : new Error('Invalid voice event'));
    }
  }

  private resume(durableOffsetMs: number) {
    this.dropDurableAudio(durableOffsetMs);
    this.ready = true;
    this.disconnectedAt = undefined;
    this.deps.onStatus?.('connected');
    for (const frame of this.frames) this.socket?.send(frame.data);
    if (this.heartbeatTimer !== undefined) this.stopHeartbeat(this.heartbeatTimer);
    this.heartbeatTimer = this.startHeartbeat(
      () => this.sendControl({ type: 'ping' }),
      this.session.heartbeatSeconds * 1_000,
    );
  }

  private dropDurableAudio(durableOffsetMs: number) {
    this.frames = this.frames.filter((frame) => frame.endMs > durableOffsetMs);
  }

  private disconnected(socket: SocketLike) {
    if (socket !== this.socket) return;
    this.socket = undefined;
    this.ready = false;
    if (this.heartbeatTimer !== undefined) this.stopHeartbeat(this.heartbeatTimer);
    this.heartbeatTimer = undefined;
    if (this.stopped) return;
    this.disconnectedAt ??= this.now();
    if (this.reconnectGraceExpired()) {
      this.fail(new Error('Voice reconnect grace period expired'));
      return;
    }
    this.deps.onStatus?.('reconnecting');
    this.reconnectTimer = this.schedule(() => void this.reconnect(), 500);
  }

  private async reconnect() {
    this.reconnectTimer = undefined;
    if (this.stopped) return;
    if (this.reconnectGraceExpired()) {
      this.fail(new Error('Voice reconnect grace period expired'));
      return;
    }
    try {
      this.ticket = await this.deps.refreshTicket(this.session.sessionId);
      this.connect();
    } catch {
      this.reconnectTimer = this.schedule(() => void this.reconnect(), 1_000);
    }
  }

  private reconnectGraceExpired(): boolean {
    return (
      this.disconnectedAt !== undefined &&
      this.now() - this.disconnectedAt >= this.session.reconnectGraceSeconds * 1_000
    );
  }

  private sendControl(message: object) {
    if (this.socket?.readyState === OPEN) this.socket.send(JSON.stringify(message));
  }

  private clearTimers() {
    if (this.reconnectTimer !== undefined) this.cancelSchedule(this.reconnectTimer);
    if (this.heartbeatTimer !== undefined) this.stopHeartbeat(this.heartbeatTimer);
    this.reconnectTimer = undefined;
    this.heartbeatTimer = undefined;
  }

  private fail(error: Error) {
    this.stopped = true;
    this.clearTimers();
    this.socket?.close(1011);
    this.deps.onError?.(error);
    this.deps.onStatus?.('closed');
  }
}
