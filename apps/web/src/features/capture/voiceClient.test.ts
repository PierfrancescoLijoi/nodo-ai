import { describe, expect, it, vi } from 'vitest';

import { VoiceSessionClient, type SocketLike, type VoiceSession, type VoiceSocketEvent } from './voiceClient';

class FakeSocket implements SocketLike {
  binaryType: BinaryType = 'arraybuffer';
  readyState = 0;
  sent: (string | ArrayBuffer)[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((event: VoiceSocketEvent) => void) | null = null;
  onclose: ((event: { code: number }) => void) | null = null;
  onerror: (() => void) | null = null;

  send(data: string | ArrayBuffer) {
    this.sent.push(data);
  }

  close() {
    this.readyState = 3;
  }

  open() {
    this.readyState = 1;
    this.onopen?.();
  }

  json(value: object) {
    this.onmessage?.({ data: JSON.stringify(value) });
  }

  disconnect(code = 1012) {
    this.readyState = 3;
    this.onclose?.({ code });
  }
}

const SESSION: VoiceSession = {
  sessionId: 's1',
  ticket: 'initial-ticket-that-is-long-enough',
  protocolVersion: 1,
  streamUrl: 'wss://api.test/v1/voice/stream',
  heartbeatSeconds: 15,
  reconnectGraceSeconds: 30,
};

function controls(socket: FakeSocket) {
  return socket.sent
    .filter((item): item is string => typeof item === 'string')
    .map((item) => JSON.parse(item));
}

describe('VoiceSessionClient', () => {
  it('handshakes, sends PCM frames and acknowledges reliable events', async () => {
    const socket = new FakeSocket();
    const received = vi.fn();
    const client = new VoiceSessionClient(SESSION, received, {
      createSocket: () => socket,
      refreshTicket: vi.fn(),
    });

    client.connect();
    socket.open();
    socket.json({ type: 'session.ready', audio_resume_offset_ms: 0 });
    client.sendPcm(0, new Int16Array(640));
    socket.json({ type: 'transcript.final', sequence: 1, text: 'hello' });

    expect(controls(socket)[0]).toMatchObject({
      type: 'hello',
      protocol_version: 1,
      ticket: SESSION.ticket,
      last_ack: 0,
    });
    expect(socket.sent.some((item) => item instanceof ArrayBuffer)).toBe(true);
    expect(received).toHaveBeenCalledWith(expect.objectContaining({ text: 'hello' }));
    expect(controls(socket)).toContainEqual({ type: 'ack', sequence: 1 });
  });

  it('gets a fresh ticket and replays only audio after the server durable offset', async () => {
    const first = new FakeSocket();
    const second = new FakeSocket();
    const sockets = [first, second];
    const reconnects: (() => void)[] = [];
    const refreshTicket = vi.fn().mockResolvedValue('fresh-ticket-that-is-long-enough');
    const client = new VoiceSessionClient(SESSION, vi.fn(), {
      createSocket: () => sockets.shift()!,
      refreshTicket,
      schedule: (callback) => {
        reconnects.push(callback);
        return 1;
      },
      cancelSchedule: vi.fn(),
    });

    client.connect();
    first.open();
    first.json({ type: 'session.ready', audio_resume_offset_ms: 0 });
    client.sendPcm(0, new Int16Array(640));
    client.sendPcm(40, new Int16Array(640));
    first.json({
      type: 'transcript.final',
      sequence: 3,
      text: 'durable',
      audio_offset_end_ms: 40,
    });
    first.disconnect();
    reconnects.shift()?.();
    await Promise.resolve();

    second.open();
    second.json({ type: 'session.ready', audio_resume_offset_ms: 0 });

    expect(refreshTicket).toHaveBeenCalledWith('s1');
    expect(controls(second)[0]).toMatchObject({ last_ack: 3, ticket: 'fresh-ticket-that-is-long-enough' });
    const replayed = second.sent.filter((item): item is ArrayBuffer => item instanceof ArrayBuffer);
    expect(replayed).toHaveLength(1);
    expect(new DataView(replayed[0]!).getUint32(0)).toBe(40);
  });

  it('does not reconnect after an intentional end', () => {
    const socket = new FakeSocket();
    const schedule = vi.fn();
    const client = new VoiceSessionClient(SESSION, vi.fn(), {
      createSocket: () => socket,
      refreshTicket: vi.fn(),
      schedule,
    });
    client.connect();
    socket.open();
    socket.json({ type: 'session.ready', audio_resume_offset_ms: 0 });

    client.end();
    socket.disconnect(1000);

    expect(controls(socket)).toContainEqual({ type: 'end' });
    expect(schedule).not.toHaveBeenCalled();
  });

  it('stops retrying tickets when the server reconnect grace has expired', async () => {
    const socket = new FakeSocket();
    const reconnects: (() => void)[] = [];
    let now = 100;
    const onError = vi.fn();
    const client = new VoiceSessionClient({ ...SESSION, reconnectGraceSeconds: 1 }, vi.fn(), {
      createSocket: () => socket,
      refreshTicket: vi.fn().mockRejectedValue(new Error('down')),
      now: () => now,
      onError,
      schedule: (callback) => {
        reconnects.push(callback);
        return 1;
      },
    });
    client.connect();
    socket.open();
    socket.disconnect();
    now = 1_101;

    reconnects.shift()?.();
    await Promise.resolve();

    expect(onError).toHaveBeenCalledWith(
      expect.objectContaining({ message: expect.stringContaining('grace') }),
    );
    expect(reconnects).toHaveLength(0);
  });

  it('fails closed on an invalid durable audio offset', () => {
    const socket = new FakeSocket();
    const onError = vi.fn();
    const client = new VoiceSessionClient(SESSION, vi.fn(), {
      createSocket: () => socket,
      refreshTicket: vi.fn(),
      onError,
    });
    client.connect();
    socket.open();

    socket.json({ type: 'session.ready', audio_resume_offset_ms: 'not-a-number' });

    expect(onError).toHaveBeenCalledWith(expect.objectContaining({ message: 'Invalid audio offset' }));
    expect(socket.readyState).toBe(3);
  });
});
