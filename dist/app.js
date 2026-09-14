const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
const els = Object.fromEntries([
  "meetingDate","statusDot","statusText","timer","audioHeading","audioHint","waveform","transcriptStream","emptyState","interimText","recordButton","recordLabel","demoButton","clearButton","supportNote","summaryScroll","overviewText","topicCloud","decisionList","actionList","questionList","decisionCount","actionCount","questionCount","lastUpdated","markdownButton","pdfButton","emailButton","downloadAudio","printReport","toast"
].map(id => [id, document.getElementById(id)]));

const state = { recording: false, startedAt: null, timerId: null, recognition: null, stream: null, recorder: null, chunks: [], lines: [], decisions: [], actions: [], questions: [], demoTimers: [] };
const demoLines = [
  "今天先確認新版 onboarding 的上線時間，我們希望把註冊步驟從五步縮短成三步。",
  "數據顯示手機版在驗證信箱這一步流失最高，大約有百分之二十八的使用者離開。",
  "我們決定先移除非必要的公司資料欄位，九月二十號推出第一版。",
  "請怡君負責更新介面稿，週三下班前交給工程團隊。",
  "後端的驗證信重送機制還需要釐清，延遲問題會不會影響上線？",
  "下一次會議會檢查手機版完成率，目標是提升至少十五個百分點。"
];

function setupWaveform() {
  els.waveform.innerHTML = Array.from({ length: 26 }, (_, i) => `<i style="--h:${5 + Math.sin(i * .9) * 3}px"></i>`).join("");
}

function formatClock(seconds) {
  const m = Math.floor(seconds / 60).toString().padStart(2, "0");
  const s = Math.floor(seconds % 60).toString().padStart(2, "0");
  return `${m}:${s}`;
}

function timestamp() {
  const seconds = state.startedAt ? Math.max(0, (Date.now() - state.startedAt) / 1000) : state.lines.length * 18;
  return formatClock(seconds);
}

function setRecordingUI(active) {
  state.recording = active;
  els.recordButton.classList.toggle("recording", active);
  els.statusDot.classList.toggle("active", active);
  els.recordLabel.textContent = active ? "結束錄音" : "開始錄音";
  els.statusText.textContent = active ? "錄音進行中" : state.lines.length ? "已暫停" : "尚未開始";
  els.audioHeading.textContent = active ? "正在聆聽會議內容" : "準備好就開始";
  els.audioHint.textContent = active ? "請保持此分頁開啟，逐字稿將自動捲動" : "音訊只在你的瀏覽器中處理";
}

async function startRecording() {
  try {
    state.stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    state.startedAt = state.startedAt || Date.now();
    state.chunks = [];
    if (window.MediaRecorder) {
      state.recorder = new MediaRecorder(state.stream);
      state.recorder.ondataavailable = event => event.data.size && state.chunks.push(event.data);
      state.recorder.onstop = createAudioDownload;
      state.recorder.start(1000);
    }
    initAudioMeter(state.stream);
    if (SpeechRecognition) {
      state.recognition = new SpeechRecognition();
      state.recognition.lang = "zh-TW";
      state.recognition.continuous = true;
      state.recognition.interimResults = true;
      state.recognition.onresult = handleSpeech;
      state.recognition.onerror = event => {
        if (event.error !== "aborted" && event.error !== "no-speech") showToast(`語音辨識暫時無法使用：${event.error}`);
      };
      state.recognition.onend = () => { if (state.recording) try { state.recognition.start(); } catch (_) {} };
      state.recognition.start();
    } else {
      els.supportNote.textContent = "此瀏覽器可錄音，但不支援即時語音辨識；可用 Chrome 或載入示範。";
    }
    setRecordingUI(true);
    state.timerId = setInterval(() => { els.timer.textContent = timestamp(); }, 1000);
  } catch (error) {
    showToast(error.name === "NotAllowedError" ? "請允許麥克風權限後再試一次" : "無法開啟麥克風");
  }
}

function stopRecording() {
  setRecordingUI(false);
  clearInterval(state.timerId);
  if (state.recognition) { try { state.recognition.stop(); } catch (_) {} }
  if (state.recorder && state.recorder.state !== "inactive") state.recorder.stop();
  if (state.stream) state.stream.getTracks().forEach(track => track.stop());
  els.interimText.textContent = "";
  Array.from(els.waveform.children).forEach((bar, i) => bar.style.height = `${5 + Math.sin(i * .9) * 3}px`);
}

function initAudioMeter(stream) {
  const context = new (window.AudioContext || window.webkitAudioContext)();
  const analyser = context.createAnalyser();
  analyser.fftSize = 64;
  context.createMediaStreamSource(stream).connect(analyser);
  const data = new Uint8Array(analyser.frequencyBinCount);
  const bars = [...els.waveform.children];
  function tick() {
    if (!state.recording) return context.close();
    analyser.getByteFrequencyData(data);
    bars.forEach((bar, i) => { bar.style.height = `${Math.max(4, (data[i] / 255) * 34)}px`; });
    requestAnimationFrame(tick);
  }
  tick();
}

function handleSpeech(event) {
  let interim = "";
  for (let i = event.resultIndex; i < event.results.length; i++) {
    const text = event.results[i][0].transcript.trim();
    if (event.results[i].isFinal) addTranscript(text);
    else interim += text;
  }
  els.interimText.textContent = interim ? `正在辨識：${interim}` : "";
}

function addTranscript(text) {
  if (!text) return;
  if (els.emptyState) els.emptyState.remove();
  const time = timestamp();
  state.lines.push({ text, time });
  const row = document.createElement("article");
  row.className = "utterance";
  row.innerHTML = `<time>${time}</time><p><strong>發言者</strong>${escapeHTML(text)}</p>`;
  els.transcriptStream.appendChild(row);
  els.transcriptStream.scrollTop = els.transcriptStream.scrollHeight;
  analyzeLine(text, time);
  renderBrief();
}

function analyzeLine(text, time) {
  if (/(決定|確定|共識|確認|採用|推出|定案)/.test(text) && !/[？?]|會不會|是否/.test(text)) pushUnique(state.decisions, text, time);
  if (/(負責|請|待辦|需要|週[一二三四五六日]|之前|交給|下一次)/.test(text)) pushUnique(state.actions, text, time);
  if (/[？?]|(釐清|風險|問題|還沒|是否|會不會)/.test(text)) pushUnique(state.questions, text, time);
}

function pushUnique(list, text, time) {
  if (!list.some(item => item.text === text)) list.unshift({ text, time });
}

function renderBrief() {
  const combined = state.lines.map(line => line.text).join(" ");
  if (state.lines.length) {
    const first = state.lines[Math.max(0, state.lines.length - 3)].text;
    const latest = state.lines[state.lines.length - 1].text;
    els.overviewText.textContent = state.lines.length === 1 ? latest : `目前聚焦於「${trimSentence(first, 28)}」，最新進度：${trimSentence(latest, 54)}`;
  }
  const topics = extractTopics(combined);
  els.topicCloud.innerHTML = topics.map(topic => `<span>${escapeHTML(topic)}</span>`).join("");
  renderList(els.decisionList, state.decisions, "尚未辨識到決策");
  renderList(els.actionList, state.actions, "尚未辨識到待辦");
  renderList(els.questionList, state.questions, "目前沒有未解問題");
  els.decisionCount.textContent = state.decisions.length;
  els.actionCount.textContent = state.actions.length;
  els.questionCount.textContent = state.questions.length;
  els.lastUpdated.textContent = `剛剛更新 · ${state.lines.length} 段對話`;
  els.summaryScroll.scrollTop = 0;
}

function extractTopics(text) {
  const vocab = ["onboarding","註冊流程","手機版","驗證信","上線時間","完成率","使用者","介面稿","工程團隊","產品規劃","設計","數據"];
  const found = vocab.filter(word => text.toLowerCase().includes(word.toLowerCase()));
  return found.slice(0, 5);
}

function renderList(node, items, empty) {
  node.innerHTML = items.length ? items.slice(0, 5).map(item => `<div class="brief-item">${escapeHTML(trimSentence(item.text, 62))}<time>${item.time}</time></div>`).join("") : `<p class="placeholder">${empty}</p>`;
}

function trimSentence(text, length) { return text.length > length ? `${text.slice(0, length)}…` : text; }
function escapeHTML(text) { const el = document.createElement("span"); el.textContent = text; return el.innerHTML; }

function runDemo() {
  clearDemoTimers();
  if (!state.startedAt) state.startedAt = Date.now();
  demoLines.forEach((line, index) => {
    state.demoTimers.push(setTimeout(() => addTranscript(line), index * 850));
  });
  showToast("正在播放示範會議");
}

function clearAll() {
  if (state.recording) stopRecording();
  clearDemoTimers();
  state.lines = []; state.decisions = []; state.actions = []; state.questions = []; state.startedAt = null;
  els.timer.textContent = "00:00";
  els.transcriptStream.innerHTML = `<div class="empty-state" id="emptyState"><div class="empty-icon">〽</div><strong>對話會出現在這裡</strong><p>開始錄音後，Murmur 會將語音即時轉成文字，並在右側整理會議脈絡。</p></div>`;
  els.emptyState = document.getElementById("emptyState");
  els.overviewText.textContent = "開始對話後，這裡會持續濃縮目前討論的主題與進度。";
  els.topicCloud.innerHTML = "";
  renderBrief();
  els.lastUpdated.textContent = "等待會議開始";
  els.downloadAudio.hidden = true;
  setRecordingUI(false);
}

function clearDemoTimers() { state.demoTimers.forEach(clearTimeout); state.demoTimers = []; }

function createAudioDownload() {
  if (!state.chunks.length) return;
  const blob = new Blob(state.chunks, { type: state.recorder.mimeType || "audio/webm" });
  els.downloadAudio.href = URL.createObjectURL(blob);
  els.downloadAudio.download = `murmur-meeting-${new Date().toISOString().slice(0,10)}.webm`;
  els.downloadAudio.hidden = false;
}

function getMeetingInfo() {
  return {
    title: document.getElementById("meetingTitle").value.trim() || "會議記錄",
    date: new Intl.DateTimeFormat("zh-TW", { year: "numeric", month: "long", day: "numeric", weekday: "long" }).format(new Date()),
    duration: els.timer.textContent,
    overview: state.lines.length ? els.overviewText.textContent : "本次會議尚無內容。"
  };
}

function markdownList(items) {
  return items.length ? items.map(item => `- ${item.text}`).join("\n") : "- 無";
}

function buildMarkdown() {
  const info = getMeetingInfo();
  const transcript = state.lines.length ? state.lines.map(line => `**${line.time}｜發言者**  \n${line.text}`).join("\n\n") : "尚無逐字稿。";
  return `# ${info.title}\n\n> ${info.date} · 會議長度 ${info.duration}\n\n## 會議摘要\n\n${info.overview}\n\n## 已確認的決策\n\n${markdownList(state.decisions)}\n\n## 待辦事項\n\n${markdownList(state.actions)}\n\n## 待釐清\n\n${markdownList(state.questions)}\n\n## 完整逐字稿\n\n${transcript}\n\n---\n由 Murmur 會議助理整理\n`;
}

function downloadMarkdown() {
  const info = getMeetingInfo();
  const blob = new Blob(["\ufeff", buildMarkdown()], { type: "text/markdown;charset=utf-8" });
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = `${safeFilename(info.title)}-${new Date().toISOString().slice(0, 10)}.md`;
  link.click();
  setTimeout(() => URL.revokeObjectURL(link.href), 1000);
  showToast("Markdown 會議記錄已下載");
}

function buildEmailText() {
  const info = getMeetingInfo();
  const list = (items, empty = "無") => items.length ? items.map(item => `・${item.text}`).join("\n") : `・${empty}`;
  return `主旨：${info.title}｜會議紀要｜${new Date().toLocaleDateString("zh-TW")}\n\n大家好，\n\n以下是本次「${info.title}」的會議紀要：\n\n【會議摘要】\n${info.overview}\n\n【已確認的決策】\n${list(state.decisions)}\n\n【後續待辦】\n${list(state.actions)}\n\n【待釐清事項】\n${list(state.questions)}\n\n如有遺漏或需要修正，請直接回覆補充，謝謝。`;
}

async function copyEmailText() {
  const copied = await copyText(buildEmailText());
  showToast(copied ? "Email 文字已複製，可以直接貼上" : "無法存取剪貼簿");
}

function printPdf() {
  const info = getMeetingInfo();
  const listHtml = items => items.length ? `<ul>${items.map(item => `<li>${escapeHTML(item.text)}</li>`).join("")}</ul>` : "<p>無</p>";
  const transcriptHtml = state.lines.length ? state.lines.map(line => `<div class="transcript-line"><time>${escapeHTML(line.time)}</time><span>${escapeHTML(line.text)}</span></div>`).join("") : "<p>尚無逐字稿。</p>";
  els.printReport.innerHTML = `<h1>${escapeHTML(info.title)}</h1><div class="report-meta">${escapeHTML(info.date)} · 會議長度 ${escapeHTML(info.duration)}</div><h2>會議摘要</h2><p>${escapeHTML(info.overview)}</p><h2>已確認的決策</h2>${listHtml(state.decisions)}<h2>待辦事項</h2>${listHtml(state.actions)}<h2>待釐清</h2>${listHtml(state.questions)}<h2>完整逐字稿</h2>${transcriptHtml}<footer>由 Murmur 會議助理整理</footer>`;
  els.printReport.setAttribute("aria-hidden", "false");
  window.print();
  setTimeout(() => els.printReport.setAttribute("aria-hidden", "true"), 500);
}

async function copyText(text) {
  try { await navigator.clipboard.writeText(text); return true; }
  catch (_) {
    const area = document.createElement("textarea");
    area.value = text; area.style.position = "fixed"; area.style.opacity = "0";
    document.body.appendChild(area); area.select();
    const copied = document.execCommand("copy"); area.remove(); return copied;
  }
}

function safeFilename(name) {
  return name.replace(/[\\/:*?"<>|]/g, "-").replace(/\s+/g, "-").slice(0, 60) || "meeting-notes";
}

function showToast(message) {
  els.toast.textContent = message; els.toast.classList.add("show");
  clearTimeout(showToast.timer); showToast.timer = setTimeout(() => els.toast.classList.remove("show"), 2200);
}

els.recordButton.addEventListener("click", () => state.recording ? stopRecording() : startRecording());
els.demoButton.addEventListener("click", runDemo);
els.clearButton.addEventListener("click", clearAll);
els.markdownButton.addEventListener("click", downloadMarkdown);
els.pdfButton.addEventListener("click", printPdf);
els.emailButton.addEventListener("click", copyEmailText);
els.meetingDate.textContent = new Intl.DateTimeFormat("zh-TW", { month: "long", day: "numeric", weekday: "short" }).format(new Date());
if (!SpeechRecognition) els.supportNote.textContent = "建議使用 Chrome 以取得即時語音辨識。";
setupWaveform();
