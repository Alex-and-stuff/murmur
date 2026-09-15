# Bounded Hierarchical Meeting Memory 實作計畫

## 1. 文件目的

本文件定義即時會議摘要系統的下一階段重構規格，供 Codex 讀取現有 repository 後實作。

現有系統已具備：

- Audio → VAD → ASR pipeline
- 由 VAD 切分語音並產生 ASR segments
- 以「舊摘要 + 新 ASR → 新摘要」方式持續更新線上摘要
- 本地 8B 模型，預計以約 3072 tokens 作為每次 rollout 的 application-level context budget
- 可使用公司 Qwen3.5 122B-A10B API 執行會議結束後的高品質彙整

本次改造的核心是將無限增長的 rolling summary，改為固定大小的 online meeting state、可封存的 topic sections，以及輕量 section index。

---

## 2. 問題描述

目前更新方式可表示為：

```text
S(t) = LLM(S(t-1) + ASR(t))
```

即使每次都重新摘要，`S(t-1)` 仍可能隨會議時間增長，最後超出 8B 模型的 context window；反覆執行 summary-to-summary 也會造成資訊逐輪流失或語意漂移。

需要滿足以下條件：

1. 單場會議持續數小時時，每次 inference 的 context 不得線性增長。
2. 舊 topic 的完整內容封存後，不應在每輪 prompt 中重複傳入。
3. 模型仍須知道前文討論過哪些 topic。
4. 會議回到舊 topic 時，系統須能載回該 section 並繼續更新。
5. 所有原始 ASR 與完整 section 必須保留，可追溯且不可被靜默截斷。

---

## 3. Scope

### 3.1 本次需要修改

- ASR 結果進入 summarizer 之後的資料流
- Meeting state 與 section 資料結構
- Prompt/context builder
- Token budget 管理
- Structured LLM output 與 schema validation
- Topic operation：`continue`、`new_section`、`return_to_section`
- Section archive 與 lightweight index
- Rollout batching policy
- Overflow protection、logging、測試
- Meeting-end final consolidation interface

### 3.2 本次不得修改

- Audio capture
- VAD 模型、切分邏輯與參數
- ASR 模型與既有串流流程
- 原始 transcript 的保存方式，除非為相容新資料結構所需的最小變更

VAD 負責 speech segmentation；summarizer rollout policy 負責決定何時將多個 ASR segments 批次送入 8B。兩者必須保持解耦。

---

## 4. 目標架構

```text
Audio
  ↓
Existing VAD
  ↓
Existing ASR
  ↓
Pending ASR Segments
  ↓ rollout policy
Context Builder
  ├─ Instructions / output schema
  ├─ Lightweight section index
  ├─ Current working section
  └─ Pending ASR segments
  ↓ bounded to configured token budget
Local 8B Meeting State Updater
  ↓ structured state update
  ├─ continue            → update current section
  ├─ new_section         → freeze current; create new section
  └─ return_to_section   → archive current; load target section
  ↓
Meeting State + Section Archive + Logs
  ↓ meeting end
Final Consolidator（Qwen 122B interface）
```

Online summary 不再是最終文件，而是固定大小、供下一輪推理使用的 working state。

---

## 5. 建議資料模型

實際類別名稱可依 repository 慣例調整，但語意與欄位應保留。優先使用 Pydantic 或專案既有 schema framework。

```python
from datetime import datetime
from enum import Enum
from typing import Literal
from pydantic import BaseModel, Field


class TopicOperation(str, Enum):
    CONTINUE = "continue"
    NEW_SECTION = "new_section"
    RETURN_TO_SECTION = "return_to_section"


class ActionItem(BaseModel):
    description: str
    owner: str | None = None
    due_at: datetime | None = None
    status: Literal["open", "done", "cancelled"] = "open"


class MeetingSection(BaseModel):
    section_id: str
    title: str
    summary: str
    key_points: list[str] = Field(default_factory=list)
    decisions: list[str] = Field(default_factory=list)
    action_items: list[ActionItem] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime
    source_segment_ids: list[str] = Field(default_factory=list)


class SectionIndexEntry(BaseModel):
    section_id: str
    title: str
    short_descriptor: str


class ASRSegment(BaseModel):
    segment_id: str
    text: str
    started_at: float | None = None
    ended_at: float | None = None
    speaker_id: str | None = None


class MeetingState(BaseModel):
    meeting_id: str
    current_section_id: str | None = None
    current_section: MeetingSection | None = None
    section_index: list[SectionIndexEntry] = Field(default_factory=list)
    archived_section_ids: list[str] = Field(default_factory=list)
    pending_segments: list[ASRSegment] = Field(default_factory=list)
    state_version: int = 0


class StateUpdate(BaseModel):
    operation: TopicOperation
    target_section_id: str | None = None
    title: str
    summary: str
    short_descriptor: str
    key_points: list[str] = Field(default_factory=list)
    decisions: list[str] = Field(default_factory=list)
    action_items: list[ActionItem] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    consumed_segment_ids: list[str]
```

### 資料不變條件

- `section_index` 只存標題與極短 descriptor，不存完整摘要。
- 完整 section 存在 archive/persistence layer。
- 每個 section 記錄來源 `segment_id`，支援追溯。
- 原始 ASR segments 不因摘要完成而刪除。
- State update 應有版本號或 transaction guard，避免並行 rollout 覆寫新狀態。

---

## 6. Rollout 與 batching policy

不要每收到一個 VAD segment 就呼叫一次模型。VAD segments 應先累積在 `pending_segments`。

建議觸發條件：

```python
should_rollout = (
    pending_token_count >= rollout_trigger_tokens
    or elapsed_since_last_rollout >= rollout_max_interval_seconds
)
```

初始建議值：

```yaml
meeting_summary:
  rollout_trigger_tokens: 500
  rollout_max_interval_seconds: 60
  rollout_min_tokens: 40
```

注意事項：

- `rollout_min_tokens` 用於避免短暫停頓造成過碎 inference。
- Meeting 結束、使用者手動要求更新，或長時間 silence 時，應允許 force flush。
- Rollout 失敗時不可移除 pending segments；成功且 state 已原子性寫入後才標記 consumed。
- 同一場 meeting 同時間最多允許一個 state update in flight，或使用版本檢查實作 optimistic concurrency control。

---

## 7. Token Budget Manager

### 7.1 預算原則

若 `3072` 是模型的 input + output 總 context，必須保留 output 空間。建議初始設定：

```yaml
meeting_summary:
  max_context_tokens: 3072
  output_reserve_tokens: 512
  max_input_tokens: 2560

  max_instruction_tokens: 300
  max_index_tokens: 350
  max_current_section_tokens: 650
  max_pending_asr_tokens: 1000
  safety_margin_tokens: 260
```

上述數值必須可設定，不得散落 hard-code。

若 backend API 將 input/output 限制分開，仍以 application-level 3072 預算運行，但應保留模型真正 context window 的 headroom。

### 7.2 Context 組成

```text
Instructions / schema     約 250–300 tokens
Section index             最多 350 tokens
Current section           最多 650 tokens
Pending ASR               最多 1000 tokens
Safety margin             約 260 tokens
Output reserve            512 tokens
Total                     <= 3072 tokens
```

Token 計數必須使用與實際 inference backend 相符的 tokenizer；若無法取得完全相符的 tokenizer，使用保守估算並提高 safety margin。

### 7.3 建構流程

```python
context = context_builder.build(
    section_index=state.section_index,
    current_section=state.current_section,
    pending_segments=state.pending_segments,
    max_input_tokens=config.max_input_tokens,
)

assert tokenizer.count(context) <= config.max_input_tokens
```

不得依賴模型 API 自動截斷。

---

## 8. Prompt 與 structured output

模型任務是更新 meeting state，不是撰寫完整會議記錄。

Prompt 至少應包含以下規則：

```text
Given:
1. A lightweight index of previous meeting sections
2. The full current working section
3. Newly transcribed ASR segments

Update the meeting state while preserving decisions, action items,
open questions, important technical facts, and unresolved issues.

Classify the update as exactly one operation:
- continue: the new transcript continues the current topic
- new_section: the transcript starts a meaningfully different topic
- return_to_section: the transcript returns to a prior indexed topic

Keep the current section within its configured token budget.
Remove repetition, small talk, and obsolete intermediate reasoning.
Never invent owners, due dates, decisions, or technical facts.
Return only data conforming to the provided schema.
```

### Operation 行為

#### `continue`

- 更新目前 section。
- 合併重複項目。
- 保留尚未解決的問題、決策與 action items。
- 不建立新 section。

#### `new_section`

- 將目前 section 原子性封存。
- 使用模型回傳或 backend 產生的 `short_descriptor` 更新 index。
- 建立新的 current section。
- 新 section 僅消化本輪屬於新 topic 的 ASR。

#### `return_to_section`

- `target_section_id` 必須存在於 index/archive。
- 先封存目前 section，再載入 target section 的完整內容。
- 將新 ASR 套用至該 section。
- 不可只依 index descriptor 重建舊 section。

### 跨 topic ASR batch

單一 batch 可能同時包含舊 topic 尾端與新 topic 開頭。MVP 可先限制一次只輸出一個 operation，但必須保留所有 source segment IDs。若測試顯示資訊錯置，第二階段將輸出改為 ordered operations：

```json
{
  "operations": [
    {"operation": "continue", "segment_ids": ["s101", "s102"]},
    {"operation": "new_section", "segment_ids": ["s103", "s104"]}
  ]
}
```

---

## 9. Section Freeze、Archive 與 Retrieval

Freeze 流程：

```text
Current Section
  ↓ validate and persist full content
Section Archive
  ↓ create/update short descriptor
Section Index
  ↓
Create or load next Current Section
```

每輪 prompt 平常只看到：

```text
1. Project Status — prototype completed; testing pending
2. Control Architecture — MPC → BMS → HVAC
3. Fallback — BMS authority; timeout; manual override
```

舊 section 的完整內容僅在以下情況載入：

- 模型回傳 `return_to_section`
- 使用者明確詢問或修改先前 topic
- meeting-end final consolidation
- 除錯、稽核或人工檢視

`return_to_section` 可先以 section index 作為輕量 retrieval；若 topic 數量增加或判斷準確率不足，再加入 keyword/embedding rerank，不列為 MVP 必要項目。

---

## 10. Overflow Protection 與 Compaction

每次 inference 前執行 hard guard：

```python
if token_count(prompt) > max_input_tokens:
    prompt = compact_to_budget(prompt)

if token_count(prompt) > max_input_tokens:
    raise ContextBudgetExceeded(...)
```

建議 compaction 順序：

1. 僅保留可容納的 pending ASR，但未送出的舊 segments 留在 queue，下一輪繼續處理。
2. 去除 index descriptor 的冗字與重複資訊。
3. 將較舊 index entries 分組成 hierarchical index，保留近期 entries 詳細 descriptor。
4. 對 current section 執行顯式 compact operation，完成並驗證後才取代原狀態。
5. 若仍超限，停止該輪並留下可觀測錯誤；不得 silent truncate。

禁止：

- 靜默截斷 current section
- 丟棄尚未 consumed 的 ASR segments
- 只因模型輸出格式錯誤便覆寫原 state
- 在未保留 archive 的情況下壓縮唯一副本

---

## 11. 超長會議的 Hierarchical Index

此功能可列為第二階段，不阻擋 MVP。

當 `section_index` 超過 `max_index_tokens`：

```text
Technical Discussion
  - architecture / MPC / sensors / fallback

Deployment
  - maintenance / responsibility / field test / schedule

Recent Sections
  17. Maintenance Responsibility — approval boundary
  18. Field Deployment — commissioning workflow
  19. Test Schedule — September validation
```

較舊 sections 形成群組摘要，近期 sections 保留獨立 descriptor。完整 archive 不受影響。

---

## 12. Meeting Finalization

線上 8B 只負責 state management；會議結束後才產生正式 minutes。

Final consolidator 輸入：

- 依順序排列的完整 section archive
- current section 的最終版本
- 已彙總的 decisions
- action items
- risks / open questions
- 必要的來源 segment references

輸出：

- Executive Summary
- Discussion Summary by Topic
- Decisions
- Action Items（owner、due date、status；未知不得臆測）
- Risks
- Open Questions
- Traceability references（若產品需要）

先定義 provider-neutral interface；Qwen3.5 122B-A10B API adapter 可獨立實作與測試。若 final consolidation 失敗，不影響已保存的 online state 與 archive。

---

## 13. Persistence 與可靠性

建議每次成功 rollout 以 transaction 寫入：

1. 新/更新後的 section
2. section index
3. consumed segment IDs
4. state version
5. token usage 與 operation metadata

需處理：

- Process restart 後可從最後成功 state 繼續。
- LLM timeout 或 schema validation failure 時可重試，且不重複消化 segments。
- 每個 state update 具 idempotency key，例如 `meeting_id + state_version + segment_range`。
- Archive 與 index 的 referential integrity。
- Meeting 結束前 force flush pending segments。

---

## 14. Observability

每次 rollout 至少記錄：

```text
meeting_id
state_version_before / after
pending_segment_count
pending_asr_tokens
instruction_tokens
index_tokens
current_section_tokens
total_input_tokens
output_tokens
operation
target_section_id
compaction_applied
latency_ms
retry_count
validation_error（如有）
```

不得在一般 production log 中完整輸出逐字稿、prompt 或會議敏感內容；詳細內容僅能進入受控的 debug/audit 儲存。

建議 metrics：

- context budget utilization
- rollouts per meeting hour
- schema failure rate
- topic operation distribution
- return-to-section accuracy（離線標註測試）
- compaction frequency
- unprocessed pending queue size

---

## 15. 實作階段

### Phase 0：Repo inspection 與設計對照

Codex 先完成下列工作，尚不改變行為：

1. 找出 VAD → ASR → summarizer 的 boundary。
2. 找出目前 rolling summary state、prompt、LLM client 與 persistence 位置。
3. 確認模型 context 的 3072 tokens 是 input-only 或 input + output。
4. 確認實際 tokenizer、串流並行模型及 meeting-end lifecycle。
5. 提出預計修改檔案與相容性風險。

### Phase 1：Schema 與 token instrumentation

1. 新增 `MeetingState`、`MeetingSection`、`SectionIndexEntry`、`StateUpdate`。
2. 加 tokenizer abstraction。
3. 在不改摘要行為的前提下記錄現有 prompt/input/output tokens。
4. 加 config 與基本單元測試。

### Phase 2：Bounded state update MVP

1. 實作 pending ASR segment batching；保留既有 VAD/ASR。
2. 新增 `ContextBudgetManager` 與 `ContextBuilder`。
3. 將 prompt 改為 structured state update。
4. 實作 schema validation、retry 與 failure preservation。
5. 實作 `continue`。

### Phase 3：Topic lifecycle

1. 實作 `new_section` 與 freeze/archive/index update。
2. 實作 `return_to_section` 與 archive reload。
3. 加 transaction/idempotency protection。
4. 驗證所有 ASR segment 的 traceability。

### Phase 4：Overflow 與長時間測試

1. 實作 hard guard 與明確 compaction。
2. 建立 2–4 小時模擬 transcript integration test。
3. 驗證 context 大小不隨 meeting duration 線性增長。
4. 注入 LLM timeout、invalid JSON、restart 等 fault cases。

### Phase 5：Final consolidation

1. 建立 provider-neutral finalizer interface。
2. 先以 fake/stub provider 測試。
3. 再接 Qwen3.5 122B-A10B API。
4. 加 meeting-end force flush 與 final output persistence。

### Phase 6：Optional enhancements

- Hierarchical section index
- Ordered multi-operation output for cross-topic batches
- Keyword/embedding retrieval 與 reranker
- 人工修正 section/title/action item 的 UI/API
- Offline evaluation dashboard

---

## 16. 測試案例

### Unit tests

- Token budget 計算與各區塊配額。
- Pending segments 達 token/time threshold 時觸發 rollout。
- `continue` 正確更新 current section。
- `new_section` 正確封存、建 index、建立新 section。
- `return_to_section` 正確載入完整 archive。
- Invalid section ID 被拒絕且 state 不變。
- Invalid JSON/schema output 不覆寫 state。
- Pending segments 只在成功 commit 後標記 consumed。
- Compaction 不丟失原始 ASR 或 archive。

### Integration tests

1. **持續同 topic 30 分鐘**：current section 保持在上限內。
2. **多 topic 2–4 小時**：每輪 input 均小於設定 budget。
3. **回到舊 topic**：能正確找回並更新舊 section。
4. **快速切換 topic**：不遺失跨 batch ASR segments。
5. **極長 VAD segment / ASR 爆量**：分批處理，不截斷未處理資料。
6. **LLM timeout / rate limit**：可重試，state 不重複更新。
7. **模型輸出 hallucinated section ID**：拒絕或安全 fallback。
8. **服務重啟**：從最後 committed state 繼續。
9. **meeting end 尚有 pending segments**：force flush 後再 finalize。
10. **敏感資料 logging**：一般 log 不含完整逐字稿或 prompt。

### 離線品質評估

以人工標註 transcript 驗證：

- Topic boundary precision/recall
- Return-to-section accuracy
- Decision retention
- Action item precision/recall
- Owner/due-date hallucination rate
- 重要資訊經多輪 rollout 後的 retention rate

---

## 17. Acceptance Criteria

- [ ] 保留既有 VAD 與 ASR 行為，無串流回歸。
- [ ] Meeting duration 不會使 online LLM input context 線性增長。
- [ ] 每次 inference 均在設定的 input/context budget 內。
- [ ] 若 3072 為總 context，至少保留設定的 output tokens 與 safety margin。
- [ ] Archived section 完整內容平常不出現在 online prompt。
- [ ] Previous topics 可透過 lightweight section index 被辨識。
- [ ] 系統支援 `continue`、`new_section`、`return_to_section`。
- [ ] 回到先前 topic 時載入的是 archive 完整內容，而非由 descriptor 猜測重建。
- [ ] Current section、archive、原始 ASR 不會被 silent truncate。
- [ ] 模型錯誤、timeout 或 invalid structured output 不會破壞已提交 state。
- [ ] 未成功消化的 pending ASR segments 不會遺失。
- [ ] 所有 section 可追溯至 source segment IDs。
- [ ] 多小時 meeting 的 integration test 通過。
- [ ] Token usage、operation、compaction 與錯誤均可觀測。
- [ ] Meeting 結束時會先 flush pending segments，再執行 final consolidation。

---

## 18. Codex 執行指令

將本文件交給 Codex 時，可搭配以下指令：

```text
請先閱讀 IMPLEMENTATION_PLAN.md 與 repository 現有程式碼。

第一步只做 Phase 0：說明現有 data flow、相關檔案、目前 rolling
summary 的 state/prompt/persistence 位置，以及你建議的最小修改邊界。
不要改動既有 VAD 與 ASR pipeline。

接著提出可逐步 review 的 implementation plan。每個 phase 應保持現有
系統可執行，並補上對應測試。未確認 3072 tokens 是總 context 或
input-only 前，請採較保守的 input + output 共用預算設計。

實作時不得使用模型 API 的 silent truncation，也不得在 state commit
成功前清除 pending ASR segments。
```

---

## 19. 最終設計原則

```text
Online State      = 固定大小，供下一輪 8B 推理
Current Section   = 唯一保留完整細節的工作區塊
Section Index     = 前文標題 + 極短 descriptor
Section Archive   = 已完成 topic 的完整且不可丟失內容
Original ASR      = 永久追溯來源
Final Summary     = 會議結束後由較大模型離線彙整
```

3072 tokens 並不是整場會議的容量，而是每次 rollout 的固定 working-context budget。只要 section archive、輕量 index、pending batching 與 hard guard 正確實作，會議持續時間就不應成為 online context 的主要限制。
