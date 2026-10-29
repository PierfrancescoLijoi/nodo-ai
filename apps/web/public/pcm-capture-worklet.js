class NodoPcmCapture extends AudioWorkletProcessor {
  process(inputs) {
    const channel = inputs[0] && inputs[0][0];
    if (!channel || channel.length === 0) return true;
    const samples = new Float32Array(channel);
    let energy = 0;
    for (const sample of samples) energy += sample * sample;
    this.port.postMessage({ samples, level: Math.min(1, Math.sqrt(energy / samples.length) * 4) }, [
      samples.buffer,
    ]);
    return true;
  }
}

registerProcessor('nodo-pcm-capture', NodoPcmCapture);
