const SAMPLE_RATE = 24_000;
const HEADER_BYTES = 9; // protocol: kind byte, turn uint32, index uint32

export class PcmPlayback {
  private context?: AudioContext;
  private cursor = 0;
  private sources = new Set<AudioBufferSourceNode>();

  play(frame: ArrayBuffer) {
    if (frame.byteLength <= HEADER_BYTES) return;
    this.context ??= new AudioContext();
    const pcm = new Int16Array(frame.slice(HEADER_BYTES));
    const buffer = this.context.createBuffer(1, pcm.length, SAMPLE_RATE);
    const channel = buffer.getChannelData(0);
    for (let index = 0; index < pcm.length; index += 1) channel[index] = (pcm[index] ?? 0) / 32_768;
    const source = this.context.createBufferSource();
    source.buffer = buffer;
    source.connect(this.context.destination);
    source.onended = () => this.sources.delete(source);
    const at = Math.max(this.context.currentTime, this.cursor);
    source.start(at);
    this.cursor = at + buffer.duration;
    this.sources.add(source);
  }

  close() {
    for (const source of this.sources) source.stop();
    this.sources.clear();
    const context = this.context;
    this.context = undefined;
    this.cursor = 0;
    if (context && context.state !== 'closed') void context.close();
  }
}
