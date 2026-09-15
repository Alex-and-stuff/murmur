const TARGET_SAMPLE_RATE = 16000;
const MIN_CHUNK_SECONDS = 1;
const MAX_CHUNK_SECONDS = 16;
const SPEECH_RMS_THRESHOLD = 0.012;
const SILENCE_HANGOVER_SECONDS = 0.35;
const PRE_ROLL_FRAMES = 2;
const ROLLING_SUMMARY_DEBOUNCE_MS = 1600;
const INITIAL_SUMMARY_SEGMENTS = 2;
const FULL_SUMMARY_REBASE_SEGMENTS = 6;
const FULL_SUMMARY_REBASE_MAX_CHARS = 20000;

const ids = ["uploadButton","fileInput","youtubeForm","youtubeUrl","youtubeSubmit","video","demoAudio","demoVisual","videoBadge","playButton","playIcon","currentTime","duration","timeline","soundButton","restartButton","mediaTitle","mediaMeta","statusPill","statusText","progressText","lineCount","progressBar","transcriptStream","liveDraft","draftText","draftTime","copyButton","toast","visualWave","runtimeLabel","summaryButton","summaryContent","summaryMeta","summaryRuntime","copySummaryButton"];
const el = Object.fromEntries(ids.map(id => [id, document.getElementById(id)]));
let activeMedia = el.demoAudio;
let uploadUrl = null;
let segments = [];
let serverStatus = "loading";
let summaryStatus = "loading";
let summaryResult = null;
let summaryPending = false;
let summaryTimer = null;
let summaryRequestNewSegments = 0;
let coveredSegmentIds = new Set();
let nextSegmentId = 1;
let lastFullSummarySegmentCount = 0;
let sessionGeneration = 0;
let pendingRequests = 0;
let healthTimer = null;
let inferenceRange = null;

class RealtimeCapture {
  constructor() {
    this.context = null;
    this.nodes = new Map();
    this.samples = [];
    this.sampleCount = 0;
    this.captureStart = null;
    this.hasSpeech = false;
    this.silenceSeconds = 0;
    this.preRoll = [];
    this.requestChain = Promise.resolve();
  }

  async attach(media) {
    if (!this.context) this.context = new AudioContext({ latencyHint: "interactive" });
    await this.context.resume();
    if (this.nodes.has(media)) return;
    const source = this.context.createMediaElementSource(media);
    const processor = this.context.createScriptProcessor(4096, 2, 1);
    const silent = this.context.createGain();
    silent.gain.value = 0;
    source.connect(this.context.destination);
    source.connect(processor);
    processor.connect(silent);
    silent.connect(this.context.destination);
    processor.onaudioprocess = event => this.receive(media, event.inputBuffer);
    this.nodes.set(media, { source, processor, silent });
  }

  receive(media, input) {
    if (media !== activeMedia || media.paused || media.ended || serverStatus === "error") return;
    const mono = new Float32Array(input.length);
    for (let channel = 0; channel < input.numberOfChannels; channel++) {
      const data = input.getChannelData(channel);
      for (let i = 0; i < data.length; i++) mono[i] += data[i] / input.numberOfChannels;
    }
    const resampled = downsample(mono, this.context.sampleRate, TARGET_SAMPLE_RATE);
    const frameSeconds = resampled.length / TARGET_SAMPLE_RATE;
    const speaking = computeRMS(resampled) >= SPEECH_RMS_THRESHOLD;

    if (!speaking && !this.hasSpeech) {
      this.preRoll.push(resampled);
      if (this.preRoll.length > PRE_ROLL_FRAMES) this.preRoll.shift();
      renderDraft(0);
      return;
    }

    if (speaking) {
      this.silenceSeconds = 0;
      if (!this.hasSpeech) {
        const preRollSeconds = this.preRoll.reduce((sum, chunk) => sum + chunk.length, 0) / TARGET_SAMPLE_RATE;
        this.captureStart = Math.max(0, media.currentTime - frameSeconds - preRollSeconds);
        for (const chunk of this.preRoll) { this.samples.push(chunk); this.sampleCount += chunk.length; }
        this.preRoll = [];
        this.hasSpeech = true;
      }
    } else {
      this.silenceSeconds += frameSeconds;
    }

    this.samples.push(resampled);
    this.sampleCount += resampled.length;
    renderDraft(this.sampleCount / TARGET_SAMPLE_RATE);

    const bufferedSeconds = this.sampleCount / TARGET_SAMPLE_RATE;
    if (this.silenceSeconds >= SILENCE_HANGOVER_SECONDS || bufferedSeconds >= MAX_CHUNK_SECONDS) {
      this.flush();
    }
  }

  flush() {
    if (!this.sampleCount) return;
    if (this.sampleCount < MIN_CHUNK_SECONDS * TARGET_SAMPLE_RATE) {
      this.discard();
      return;
    }
    const pcm = concatSamples(this.samples, this.sampleCount);
    const start = this.captureStart ?? Math.max(0, activeMedia.currentTime - this.sampleCount / TARGET_SAMPLE_RATE);
    const end = start + this.sampleCount / TARGET_SAMPLE_RATE;
    const generation = sessionGeneration;
    this.samples = [];
    this.sampleCount = 0;
    this.captureStart = null;
    this.hasSpeech = false;
    this.silenceSeconds = 0;
    this.requestChain = this.requestChain.then(() => transcribeChunk(pcm, start, end, generation));
  }

  discard() {
    this.samples = [];
    this.sampleCount = 0;
    this.captureStart = null;
    this.hasSpeech = false;
    this.silenceSeconds = 0;
    this.preRoll = [];
    renderDraft(0);
  }
}

const capturer = new RealtimeCapture();

function downsample(input, sourceRate, targetRate) {
  if (sourceRate === targetRate) return input.slice();
  const ratio = sourceRate / targetRate;
  const output = new Float32Array(Math.floor(input.length / ratio));
  for (let i = 0; i < output.length; i++) {
    const start = Math.floor(i * ratio);
    const end = Math.min(input.length, Math.floor((i + 1) * ratio));
    let sum = 0;
    for (let j = start; j < end; j++) sum += input[j];
    output[i] = sum / Math.max(1, end - start);
  }
  return output;
}

function computeRMS(samples) {
  let sumSquares = 0;
  for (let i = 0; i < samples.length; i++) sumSquares += samples[i] * samples[i];
  return Math.sqrt(sumSquares / samples.length);
}

function concatSamples(chunks, length) {
  const output = new Float32Array(length);
  let offset = 0;
  for (const chunk of chunks) { output.set(chunk, offset); offset += chunk.length; }
  return output;
}

async function transcribeChunk(pcm, start, end, generation) {
  pendingRequests++;
  inferenceRange = { start, end };
  renderDraft(0, start, end);
  try {
    const response = await fetch("/api/transcribe", {
      method: "POST",
      headers: {
        "Content-Type": "application/octet-stream",
        "X-Audio-Start": String(start),
        "X-Audio-End": String(end),
        "X-Language": "Chinese"
      },
      body: pcm
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.detail || payload.error || "辨識失敗");
    if (generation !== sessionGeneration) return;
    const text = String(payload.text || "").trim();
    if (text) {
      segments.push({ id: nextSegmentId++, start: payload.start, end: payload.end, text, latency: payload.inference_seconds });
      segments.sort((a, b) => a.start - b.start);
      renderCompleted();
      scheduleRollingSummary();
      renderSummary();
    }
  } catch (error) {
    showToast(`逐字稿暫時無法產生：${error.message}`);
    checkHealth();
  } finally {
    pendingRequests--;
    inferenceRange = null;
    if (!pendingRequests) renderDraft(0);
    syncUI();
  }
}

function formatTime(value) {
  if (!Number.isFinite(value)) return "00:00";
  const minutes = Math.floor(value / 60).toString().padStart(2, "0");
  const seconds = Math.floor(value % 60).toString().padStart(2, "0");
  return `${minutes}:${seconds}`;
}

function setupWave() {
  el.visualWave.innerHTML = Array.from({ length: 44 }, (_, index) => `<i style="--h:${18 + Math.sin(index * .72) * 12 + Math.cos(index * .27) * 8}px"></i>`).join("");
}

function syncUI() {
  const current = activeMedia.currentTime || 0;
  const total = activeMedia.duration || 92.54;
  const ratio = Math.min(1, current / total);
  el.currentTime.textContent = formatTime(current);
  el.duration.textContent = formatTime(total);
  el.timeline.value = ratio * 100;
  el.timeline.style.setProperty("--progress", `${ratio * 100}%`);
  el.lineCount.textContent = `${segments.length} 段`;
  el.progressBar.style.width = `${ratio * 100}%`;
  const latest = segments.at(-1);
  el.progressText.textContent = pendingRequests ? "模型正在辨識" : latest ? `已辨識至 ${formatTime(latest.end)}` : activeMedia.paused ? "尚未開始" : "正在收音";
  el.demoVisual.classList.toggle("playing", activeMedia === el.demoAudio && !activeMedia.paused);
  el.summaryButton.disabled = summaryPending || summaryStatus !== "ready" || !segments.length;
  el.summaryButton.textContent = summaryPending ? "更新中…" : summaryResult ? "立即重整" : "立即產生";
  el.copySummaryButton.disabled = !summaryResult;
}

function renderCompleted() {
  if (!segments.length) {
    el.transcriptStream.innerHTML = `<div class="empty-state"><div class="empty-glyph">Aa</div><strong>按下播放，開始即時逐字稿</strong><p>每段聲音會送到本機模型辨識，結果會持續出現在這裡。</p></div>`;
    return;
  }
  el.transcriptStream.innerHTML = segments.map((segment, index) => `<article class="transcript-line${index === segments.length - 1 ? " latest" : ""}" data-start="${segment.start}"><button type="button" aria-label="跳到 ${formatTime(segment.start)}">${formatTime(segment.start)}</button><p>${escapeHTML(segment.text)}</p></article>`).join("");
  el.transcriptStream.querySelectorAll("article").forEach(row => row.addEventListener("click", () => { capturer.discard(); activeMedia.currentTime = Number(row.dataset.start); syncUI(); }));
  requestAnimationFrame(() => { el.transcriptStream.scrollTop = el.transcriptStream.scrollHeight; });
}

function renderDraft(bufferedSeconds = 0, start = null, end = null) {
  if (pendingRequests) {
    const range = inferenceRange || { start, end };
    el.liveDraft.hidden = false;
    el.draftTime.textContent = formatTime(range.start ?? activeMedia.currentTime);
    el.draftText.innerHTML = `正在辨識 ${formatTime(range.start)}–${formatTime(range.end)} 的聲音<i class="caret"></i>`;
    return;
  }
  if (activeMedia.paused || !bufferedSeconds) { el.liveDraft.hidden = true; return; }
  el.liveDraft.hidden = false;
  el.draftTime.textContent = formatTime(Math.max(0, activeMedia.currentTime - bufferedSeconds));
  el.draftText.innerHTML = `正在聆聽，已收集 ${bufferedSeconds.toFixed(1)} 秒<i class="caret"></i>`;
}

async function togglePlay() {
  if (!activeMedia.paused) { activeMedia.pause(); return; }
  if (serverStatus !== "ready") {
    showToast(serverStatus === "loading" ? "模型仍在載入，請稍候" : "請先啟動本機 inference service");
    await checkHealth();
    return;
  }
  try {
    await capturer.attach(activeMedia);
    await activeMedia.play();
  } catch (_) { showToast("無法播放或擷取這個檔案的聲音"); }
}

function updatePlaybackState() {
  const playing = !activeMedia.paused;
  el.playIcon.textContent = playing ? "Ⅱ" : "▶";
  el.playButton.setAttribute("aria-label", playing ? "暫停" : "播放");
  el.statusPill.classList.toggle("active", playing && serverStatus === "ready");
  if (serverStatus === "loading") el.statusText.textContent = "模型載入中";
  else if (serverStatus === "error") el.statusText.textContent = "後端未連線";
  else el.statusText.textContent = playing ? "辨識中" : activeMedia.currentTime ? "已暫停" : "可以開始";
  if (!playing) capturer.flush();
  syncUI();
}

function bindMedia(media) {
  ["timeupdate", "loadedmetadata", "durationchange"].forEach(name => media.addEventListener(name, syncUI));
  ["play", "pause", "ended"].forEach(name => media.addEventListener(name, updatePlaybackState));
  media.addEventListener("seeking", () => capturer.discard());
}

function resetTranscript() {
  sessionGeneration++;
  segments = [];
  summaryResult = null;
  coveredSegmentIds = new Set();
  nextSegmentId = 1;
  lastFullSummarySegmentCount = 0;
  clearTimeout(summaryTimer);
  summaryTimer = null;
  capturer.discard();
  renderCompleted();
  renderSummary();
  syncUI();
}

function activateMediaSource(sourceUrl, { isAudio, label, meta, badgeText, toastMessage }) {
  activeMedia.pause();
  if (isAudio) {
    el.video.pause();
    el.video.classList.remove("visible");
    el.demoAudio.src = sourceUrl;
    activeMedia = el.demoAudio;
    el.demoVisual.hidden = false;
  } else {
    el.video.src = sourceUrl;
    activeMedia = el.video;
    el.video.classList.add("visible");
    el.demoVisual.hidden = true;
  }
  el.videoBadge.innerHTML = `<span></span> ${badgeText}`;
  el.mediaTitle.textContent = label;
  el.mediaMeta.textContent = meta;
  resetTranscript();
  updatePlaybackState();
  showToast(toastMessage);
}

function loadMedia(file) {
  if (uploadUrl) URL.revokeObjectURL(uploadUrl);
  uploadUrl = URL.createObjectURL(file);
  const isAudio = file.type.startsWith("audio/") || /\.(mp3|wav|m4a|aac|ogg|flac)$/i.test(file.name);
  activateMediaSource(uploadUrl, {
    isAudio,
    label: file.name,
    meta: `${formatBytes(file.size)} · realtime inference`,
    badgeText: isAudio ? "本機音訊" : "本機影片",
    toastMessage: `${isAudio ? "音訊" : "影片"}已載入；播放中的聲音會送往本機模型`,
  });
}

async function loadYoutubeMedia(url) {
  el.youtubeUrl.disabled = true;
  el.youtubeSubmit.disabled = true;
  const originalLabel = el.youtubeSubmit.textContent;
  el.youtubeSubmit.textContent = "下載中…";
  showToast("正在從 YouTube 下載音訊，請稍候");
  try {
    const response = await fetch("/api/fetch-media", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url }),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.detail || payload.error || "下載失敗");
    if (uploadUrl) { URL.revokeObjectURL(uploadUrl); uploadUrl = null; }
    activateMediaSource(payload.url, {
      isAudio: true,
      label: payload.title || "YouTube 音訊",
      meta: "YouTube · realtime inference",
      badgeText: "YouTube 音訊",
      toastMessage: "YouTube 音訊已載入；播放中的聲音會送往本機模型",
    });
    el.youtubeUrl.value = "";
  } catch (error) {
    showToast(`YouTube 音訊載入失敗：${error.message}`);
  } finally {
    el.youtubeUrl.disabled = false;
    el.youtubeSubmit.disabled = false;
    el.youtubeSubmit.textContent = originalLabel;
  }
}

function restart() {
  activeMedia.pause();
  activeMedia.currentTime = 0;
  resetTranscript();
  updatePlaybackState();
}

async function copyTranscript() {
  const text = segments.map(segment => `[${formatTime(segment.start)}] ${segment.text}`).join("\n");
  if (!text) return showToast("播放後才有逐字稿可以複製");
  try { await navigator.clipboard.writeText(text); showToast("已複製目前的逐字稿"); } catch (_) { showToast("瀏覽器無法存取剪貼簿"); }
}

function renderSummary() {
  if (!summaryResult) {
    const waiting = segments.length && segments.length < INITIAL_SUMMARY_SEGMENTS;
    const title = summaryPending ? "正在建立第一版摘要…" : waiting ? "再多一段就開始整理" : "摘要會隨逐字稿出現在這裡";
    const detail = summaryPending ? `正在整合前 ${summaryRequestNewSegments} 段穩定逐字稿。` : waiting ? "累積足夠上下文後會自動開始。" : "每當新段落完成，系統會在背景滾動更新會議重點。";
    el.summaryContent.innerHTML = `<div class="summary-empty"><strong>${title}</strong><p>${detail}</p></div>`;
    el.summaryMeta.textContent = summaryPending ? "Real-time 滾動摘要更新中" : "自動追蹤穩定的逐字稿段落。";
    syncUI();
    return;
  }
  const list = (title, items) => items.length ? `<div class="summary-block"><h3>${title}</h3><ul>${items.map(item => `<li>${escapeHTML(item)}</li>`).join("")}</ul></div>` : "";
  const actions = summaryResult.action_items.map(item => {
    const meta = [item.owner && `負責：${item.owner}`, item.due && `期限：${item.due}`].filter(Boolean).join(" · ");
    return meta ? `${item.task}（${meta}）` : item.task;
  });
  el.summaryContent.innerHTML = `<div class="summary-grid"><div><div class="summary-block"><h3>摘要</h3><p>${escapeHTML(summaryResult.summary)}</p></div>${list("重點", summaryResult.key_points)}</div><div>${list("決策", summaryResult.decisions)}${list("待辦事項", actions)}</div></div>`;
  const uncovered = segments.filter(segment => !coveredSegmentIds.has(segment.id)).length;
  el.summaryMeta.textContent = summaryPending
    ? `正在整合 ${summaryRequestNewSegments} 段新逐字稿…`
    : uncovered
      ? `已涵蓋 ${coveredSegmentIds.size} 段 · ${uncovered} 段等待更新`
      : `已同步 ${coveredSegmentIds.size} 段逐字稿 · 上次生成 ${Number(summaryResult.inference_seconds || 0).toFixed(1)} 秒`;
  syncUI();
}

function summaryForRequest() {
  if (!summaryResult) return null;
  return {
    summary: summaryResult.summary,
    key_points: summaryResult.key_points,
    decisions: summaryResult.decisions,
    action_items: summaryResult.action_items,
  };
}

function scheduleRollingSummary({ immediate = false } = {}) {
  clearTimeout(summaryTimer);
  summaryTimer = null;
  if (summaryStatus !== "ready" || summaryPending || !segments.length) return;
  const uncovered = segments.filter(segment => !coveredSegmentIds.has(segment.id));
  const required = summaryResult ? 1 : INITIAL_SUMMARY_SEGMENTS;
  if (uncovered.length < required) return;
  summaryTimer = setTimeout(() => generateSummary(false), immediate ? 0 : ROLLING_SUMMARY_DEBOUNCE_MS);
}

async function generateSummary(forceFull = false) {
  if (!segments.length || summaryPending) return;
  clearTimeout(summaryTimer);
  summaryTimer = null;
  const generation = sessionGeneration;
  const snapshot = segments.slice();
  const transcriptChars = snapshot.reduce((total, segment) => total + segment.text.length + 32, 0);
  const rebaseDue = summaryResult
    && snapshot.length - lastFullSummarySegmentCount >= FULL_SUMMARY_REBASE_SEGMENTS
    && transcriptChars <= FULL_SUMMARY_REBASE_MAX_CHARS;
  const incremental = !forceFull && !rebaseDue && summaryResult && coveredSegmentIds.size;
  const requestSegments = incremental
    ? snapshot.filter(segment => !coveredSegmentIds.has(segment.id))
    : snapshot;
  if (!requestSegments.length) return;
  summaryPending = true;
  summaryRequestNewSegments = requestSegments.length;
  let completed = false;
  renderSummary();
  syncUI();
  try {
    const response = await fetch("/api/summarize", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        segments: requestSegments.map(({ start, end, text }) => ({ start, end, text })),
        ...(incremental ? { previous_summary: summaryForRequest() } : {}),
      }),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.detail || payload.error || "摘要失敗");
    if (generation !== sessionGeneration) return;
    summaryResult = payload;
    coveredSegmentIds = forceFull || !incremental
      ? new Set(snapshot.map(segment => segment.id))
      : new Set([...coveredSegmentIds, ...requestSegments.map(segment => segment.id)]);
    if (!incremental) lastFullSummarySegmentCount = snapshot.length;
    completed = true;
    renderSummary();
  } catch (error) {
    showToast(`摘要暫時無法產生：${error.message}`);
    checkHealth();
  } finally {
    summaryPending = false;
    summaryRequestNewSegments = 0;
    if (completed && generation === sessionGeneration) scheduleRollingSummary({ immediate: true });
    renderSummary();
    syncUI();
  }
}

async function copySummary() {
  if (!summaryResult) return;
  const sections = [
    `摘要\n${summaryResult.summary}`,
    summaryResult.key_points.length ? `重點\n${summaryResult.key_points.map(item => `- ${item}`).join("\n")}` : "",
    summaryResult.decisions.length ? `決策\n${summaryResult.decisions.map(item => `- ${item}`).join("\n")}` : "",
    summaryResult.action_items.length ? `待辦事項\n${summaryResult.action_items.map(item => `- ${item.task}${item.owner ? `（${item.owner}）` : ""}`).join("\n")}` : "",
  ].filter(Boolean).join("\n\n");
  try { await navigator.clipboard.writeText(sections); showToast("已複製摘要"); } catch (_) { showToast("瀏覽器無法存取剪貼簿"); }
}

async function checkHealth() {
  const previousSummaryStatus = summaryStatus;
  try {
    const response = await fetch("/api/health", { cache: "no-store" });
    if (!response.ok) throw new Error();
    const health = await response.json();
    serverStatus = health.status === "ready" ? "ready" : health.status === "loading" ? "loading" : "error";
    summaryStatus = health.summary?.status === "ready" ? "ready" : health.summary?.status === "loading" ? "loading" : "error";
    el.runtimeLabel.textContent = health.model || (serverStatus === "loading" ? "正在載入 Qwen3-ASR" : "Inference service 發生錯誤");
    el.summaryRuntime.textContent = health.summary?.model || (summaryStatus === "loading" ? "摘要模型載入中" : "摘要模型無法使用");
  } catch (_) {
    serverStatus = "error";
    summaryStatus = "error";
    el.runtimeLabel.textContent = "未連接本機 inference service";
    el.summaryRuntime.textContent = "未連接本機 inference service";
  }
  updatePlaybackState();
  if (previousSummaryStatus !== "ready" && summaryStatus === "ready") scheduleRollingSummary();
  clearTimeout(healthTimer);
  if (serverStatus !== "ready" || summaryStatus === "loading") healthTimer = setTimeout(checkHealth, 2000);
  return serverStatus;
}

function formatBytes(bytes) { return bytes > 1048576 ? `${(bytes / 1048576).toFixed(1)} MB` : `${Math.ceil(bytes / 1024)} KB`; }
function escapeHTML(text) { const node = document.createElement("span"); node.textContent = text; return node.innerHTML; }
function showToast(message) { el.toast.textContent = message; el.toast.classList.add("show"); clearTimeout(showToast.timer); showToast.timer = setTimeout(() => el.toast.classList.remove("show"), 3000); }

el.playButton.addEventListener("click", togglePlay);
el.demoVisual.addEventListener("click", togglePlay);
el.timeline.addEventListener("input", () => { capturer.discard(); const total = activeMedia.duration || 92.54; activeMedia.currentTime = (Number(el.timeline.value) / 100) * total; syncUI(); });
el.soundButton.addEventListener("click", () => { activeMedia.muted = !activeMedia.muted; el.soundButton.textContent = activeMedia.muted ? "×" : "⌁"; el.soundButton.classList.toggle("muted", activeMedia.muted); });
el.restartButton.addEventListener("click", restart);
el.uploadButton.addEventListener("click", () => el.fileInput.click());
el.fileInput.addEventListener("change", event => {
  if (event.target.files[0]) loadMedia(event.target.files[0]);
  event.target.value = "";
});
el.copyButton.addEventListener("click", copyTranscript);
el.summaryButton.addEventListener("click", () => generateSummary(true));
el.copySummaryButton.addEventListener("click", copySummary);
el.youtubeForm.addEventListener("submit", event => {
  event.preventDefault();
  const url = el.youtubeUrl.value.trim();
  if (!url) return showToast("請先貼上 YouTube 連結");
  loadYoutubeMedia(url);
});
document.addEventListener("keydown", event => { if (event.code === "Space" && !/INPUT|BUTTON/.test(document.activeElement.tagName)) { event.preventDefault(); togglePlay(); } });

bindMedia(el.demoAudio);
bindMedia(el.video);
setupWave();
renderCompleted();
renderSummary();
checkHealth();
