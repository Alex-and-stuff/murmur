const cues = [
  [0.0, 5.4, "來一分鐘，直接幫你整理好這次 Apple 發表會的重點。"],
  [5.4, 11.5, "首先最重磅的就是 Apple 出的第一支折疊 iPhone，叫做 iPhone Duo。"],
  [11.5, 17.2, "台灣是七萬四千九起跳，十月二十三號開賣。"],
  [17.2, 23.5, "打開來有七點六寸的大螢幕，最有感的功能都是圍繞著這塊螢幕轉。"],
  [23.5, 30.5, "用後面的主鏡頭幫別人拍照時，外面的螢幕可以同步顯示畫面。"],
  [30.5, 36.6, "被拍的人可以自己看到構圖、調整姿勢，我個人覺得真的超實用。"],
  [36.6, 42.6, "手機也會判斷大家什麼時候擺好姿勢、看著鏡頭，然後自己按下快門。"],
  [42.6, 48.1, "但它背後只有兩個鏡頭，沒有獨立長焦，拍遠的東西不強。"],
  [48.1, 54.1, "iOS 27 還讓 iPhone 第一次可以左右同時開兩個 App。"],
  [54.1, 59.5, "它也支援 Apple Pencil，這是 iPhone 史上第一次可以用筆。"],
  [59.5, 65.4, "再來多數人可能還是會買 iPhone 18 Pro 或 Pro Max。"],
  [65.4, 70.9, "台灣四萬四千九起跳，九月十八號就到貨，這一代升級幾乎都在相機。"],
  [70.9, 77.2, "它加入可變光圈，光線暗時自動開到最大，讓更多光進來。"],
  [77.2, 83.1, "拍多人合照時光圈會縮小，讓前排和後排的人可以同時清楚。"],
  [83.1, 88.2, "顏色有黑色、銀色，加上冰川藍跟勃艮第紅兩個新色。"],
  [88.2, 92.54, "那你會選哪一個呢？可以留言跟我說，我自己還在糾結到不行。"]
];

const ids = ["uploadButton","fileInput","video","demoAudio","demoVisual","videoBadge","playButton","playIcon","currentTime","duration","timeline","soundButton","restartButton","mediaTitle","mediaMeta","statusPill","statusText","progressText","lineCount","progressBar","transcriptStream","liveDraft","draftText","draftTime","copyButton","toast","visualWave"];
const el = Object.fromEntries(ids.map(id => [id, document.getElementById(id)]));
let activeMedia = el.demoAudio;
let renderedCount = 0;
let uploadUrl = null;

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
  const total = activeMedia.duration || cues[cues.length - 1][1];
  const ratio = Math.min(1, current / total);
  el.currentTime.textContent = formatTime(current);
  el.duration.textContent = formatTime(total);
  el.timeline.value = ratio * 100;
  el.timeline.style.setProperty("--progress", `${ratio * 100}%`);
  const completed = cues.filter(cue => current >= cue[1]).length;
  if (completed !== renderedCount) renderCompleted(completed);
  renderDraft(current, completed);
  el.lineCount.textContent = `${completed} / ${cues.length} 段`;
  el.progressBar.style.width = `${(completed / cues.length) * 100}%`;
  el.progressText.textContent = completed ? `已完成 ${Math.round((completed / cues.length) * 100)}%` : activeMedia.paused ? "尚未開始" : "正在建立逐字稿";
  el.demoVisual.classList.toggle("playing", activeMedia === el.demoAudio && !activeMedia.paused);
}

function renderCompleted(count) {
  renderedCount = count;
  if (!count) {
    el.transcriptStream.innerHTML = `<div class="empty-state"><div class="empty-glyph">Aa</div><strong>按下播放，看看逐字稿如何產生</strong><p>完成的句子會保留下來，目前辨識中的內容則會即時更新。</p></div>`;
    return;
  }
  el.transcriptStream.innerHTML = cues.slice(0, count).map((cue, index) => `<article class="transcript-line${index === count - 1 ? " latest" : ""}" data-start="${cue[0]}"><button type="button" aria-label="跳到 ${formatTime(cue[0])}">${formatTime(cue[0])}</button><p>${escapeHTML(cue[2])}</p></article>`).join("");
  el.transcriptStream.querySelectorAll("article").forEach(row => row.addEventListener("click", () => { activeMedia.currentTime = Number(row.dataset.start); syncUI(); }));
  requestAnimationFrame(() => { el.transcriptStream.scrollTop = el.transcriptStream.scrollHeight; });
}

function renderDraft(current, completed) {
  const cue = cues.find(item => current >= item[0] && current < item[1]);
  if (!cue || activeMedia.paused || completed >= cues.length) { el.liveDraft.hidden = true; return; }
  const progress = Math.max(0.06, (current - cue[0]) / (cue[1] - cue[0]));
  const characters = Math.max(1, Math.ceil(cue[2].length * progress));
  el.liveDraft.hidden = false;
  el.draftTime.textContent = formatTime(cue[0]);
  el.draftText.innerHTML = `${escapeHTML(cue[2].slice(0, characters))}<i class="caret"></i>`;
}

function togglePlay() { if (activeMedia.paused) activeMedia.play().catch(() => showToast("無法播放這個檔案")); else activeMedia.pause(); }
function updatePlaybackState() {
  const playing = !activeMedia.paused;
  el.playIcon.textContent = playing ? "Ⅱ" : "▶";
  el.playButton.setAttribute("aria-label", playing ? "暫停" : "播放");
  el.statusPill.classList.toggle("active", playing);
  el.statusText.textContent = playing ? "辨識中" : activeMedia.currentTime ? "已暫停" : "等待播放";
  syncUI();
}
function bindMedia(media) {
  ["timeupdate", "loadedmetadata", "durationchange"].forEach(name => media.addEventListener(name, syncUI));
  ["play", "pause", "ended"].forEach(name => media.addEventListener(name, updatePlaybackState));
}
function loadVideo(file) {
  activeMedia.pause();
  if (uploadUrl) URL.revokeObjectURL(uploadUrl);
  uploadUrl = URL.createObjectURL(file); el.video.src = uploadUrl; activeMedia = el.video;
  el.video.classList.add("visible"); el.demoVisual.hidden = true;
  el.videoBadge.innerHTML = "<span></span> 本機影片";
  el.mediaTitle.textContent = file.name; el.mediaMeta.textContent = `${formatBytes(file.size)} · 使用測試逐字稿`;
  renderedCount = -1; renderCompleted(0); updatePlaybackState();
  showToast("影片已載入，不會上傳到伺服器");
}
function restart() { activeMedia.pause(); activeMedia.currentTime = 0; renderedCount = -1; renderCompleted(0); updatePlaybackState(); }
async function copyTranscript() {
  const count = cues.filter(cue => activeMedia.currentTime >= cue[1]).length;
  const text = cues.slice(0, count).map(cue => `[${formatTime(cue[0])}] ${cue[2]}`).join("\n");
  if (!text) return showToast("播放後才有逐字稿可以複製");
  try { await navigator.clipboard.writeText(text); showToast("已複製目前的逐字稿"); } catch (_) { showToast("瀏覽器無法存取剪貼簿"); }
}
function formatBytes(bytes) { return bytes > 1048576 ? `${(bytes / 1048576).toFixed(1)} MB` : `${Math.ceil(bytes / 1024)} KB`; }
function escapeHTML(text) { const node = document.createElement("span"); node.textContent = text; return node.innerHTML; }
function showToast(message) { el.toast.textContent = message; el.toast.classList.add("show"); clearTimeout(showToast.timer); showToast.timer = setTimeout(() => el.toast.classList.remove("show"), 2200); }

el.playButton.addEventListener("click", togglePlay);
el.demoVisual.addEventListener("click", togglePlay);
el.timeline.addEventListener("input", () => { const total = activeMedia.duration || cues[cues.length - 1][1]; activeMedia.currentTime = (Number(el.timeline.value) / 100) * total; syncUI(); });
el.soundButton.addEventListener("click", () => { activeMedia.muted = !activeMedia.muted; el.soundButton.textContent = activeMedia.muted ? "×" : "⌁"; el.soundButton.classList.toggle("muted", activeMedia.muted); });
el.restartButton.addEventListener("click", restart);
el.uploadButton.addEventListener("click", () => el.fileInput.click());
el.fileInput.addEventListener("change", event => event.target.files[0] && loadVideo(event.target.files[0]));
el.copyButton.addEventListener("click", copyTranscript);
document.addEventListener("keydown", event => { if (event.code === "Space" && !/INPUT|BUTTON/.test(document.activeElement.tagName)) { event.preventDefault(); togglePlay(); } });

bindMedia(el.demoAudio); bindMedia(el.video); setupWave(); syncUI();
