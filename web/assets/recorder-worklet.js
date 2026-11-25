/* 录音用的 AudioWorklet：把麦克风数据按 4096 帧一块发回主线程。
   ScriptProcessorNode 已废弃、块大小固定且延迟更高，这里用它。
   主线程通过 port.postMessage({on: true/false}) 控制是否采集。 */

class RecorderProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.on = false;
    this.size = 4096;
    this.buf = new Float32Array(this.size);
    this.filled = 0;
    this.port.onmessage = (e) => {
      this.on = !!(e.data && e.data.on);
    };
  }

  process(inputs) {
    const input = inputs[0] && inputs[0][0];
    if (!this.on || !input) return true;
    let i = 0;
    while (i < input.length) {
      const n = Math.min(this.size - this.filled, input.length - i);
      this.buf.set(input.subarray(i, i + n), this.filled);
      this.filled += n;
      i += n;
      if (this.filled === this.size) {
        this.port.postMessage(this.buf.slice(0)); // 复制一份，别让下一轮覆盖
        this.filled = 0;
      }
    }
    return true;
  }
}

registerProcessor('homeos-recorder', RecorderProcessor);
