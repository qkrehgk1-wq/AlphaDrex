"use strict";
const $ = (s) => document.querySelector(s);
const el = (tag, text, cls) => {
  const n = document.createElement(tag);
  if (text !== undefined) n.textContent = text;
  if (cls) n.className = cls;
  return n;
};
let state,
  active = null,
  conversation = null,
  selected = null,
  busy = false,
  navigation = 0,
  authEpoch = 0,
  refreshEpoch = 0,
  settingTab = "일반";
const showError = (e) => {
  $("#error").textContent = e.message || String(e);
  $("#error").hidden = false;
};
const clearError = () => {
  $("#error").hidden = true;
};
async function api(path, body) {
  const epoch = authEpoch;
  let r;
  try {
    r = await fetch(
    path,
    body === undefined
      ? {}
      : {
          method: "POST",
          headers: { "Content-Type": "application/json", "X-AlphaDrex": "1" },
          body: JSON.stringify(body),
        },
  );
  } catch {
    throw Error("AlphaDrex 서버에 연결할 수 없습니다. 바탕화면 AlphaDrex 아이콘을 다시 눌러 주세요.");
  }
  let d;
  try {
    d = await r.json();
  } catch {
    throw Error("서버 응답을 읽지 못했습니다(" + r.status + "). 잠시 뒤 다시 시도하고, 반복되면 관리자에게 알려 주세요.");
  }
  if (epoch !== authEpoch) throw Error("로그인 상태가 변경되어 이전 요청 결과를 표시하지 않습니다.");
  if (r.status === 401 && d.login_required) showLogin();
  if (!r.ok) throw Error(d.error || "요청 실패");
  return d;
}
function action(button, fn) {
  button.addEventListener("click", async () => {
    clearError();
    try {
      await fn();
    } catch (e) {
      if ($("#settings").open) settingsError(e);
      else showError(e);
    }
  });
  return button;
}
function button(text, fn, cls) {
  return action(el("button", text, cls), fn);
}
function download(name, data, type = "application/json") {
  const url = URL.createObjectURL(
    new Blob(
      [typeof data === "string" ? data : JSON.stringify(data, null, 2)],
      { type },
    ),
  );
  const a = el("a");
  a.href = url;
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
function link(text, url) {
  const a = el("a", text);
  try {
    const u = new URL(url);
    if (["https:", "http:"].includes(u.protocol)) {
      a.href = u.href;
      a.target = "_blank";
      a.rel = "noopener noreferrer";
    }
  } catch {}
  return a;
}
function field(label, type = "text", value = "") {
  const wrap = el("label", label, "formfield");
  const input = el(type === "textarea" ? "textarea" : "input");
  if (type !== "textarea") input.type = type;
  input.value = value;
  wrap.append(input);
  return [wrap, input];
}
function select(label, options, value) {
  const wrap = el("label", label, "formfield");
  const input = el("select");
  for (const [v, t] of options) {
    const opt = el("option", t);
    opt.value = v;
    input.append(opt);
  }
  input.value = value;
  wrap.append(input);
  return [wrap, input];
}
function checkbox(label, checked = false) {
  const wrap = el("label", undefined, "check");
  const input = el("input");
  input.type = "checkbox";
  input.checked = checked;
  wrap.append(input, document.createTextNode(label));
  return [wrap, input];
}
async function refresh() {
  const epoch = ++refreshEpoch;
  const loaded = await api("/api/state");
  if (epoch !== refreshEpoch) return;
  state = loaded;
  $("#modeBadge").textContent =
    state.policy.mode === "internal" ? "내부망 · 외부 호출 차단" : "외부망";
  const live = state.connections.drex_live;
  $("#connectionSummary").textContent =
    (live === true
      ? "● AI(Drex) 준비됨"
      : live === false
        ? "● AI(Drex) 꺼짐 · 매뉴얼 키워드 검색만 가능 · 관리자에게 Drex 시작 요청"
        : "AI(Drex) 원격 주소 · 상태 미확인") +
    " · Liner 키 " +
    (state.connections.liner_key_registered ? "등록" : "미등록");
  $("#connectionSummary").className = live === true ? "ok" : live === false ? "down" : "";
  $("#userBadge").textContent = state.accounts_enabled
    ? state.user.name + (state.user.role === "admin" ? " · 관리자" : " · 근무자")
    : "계정 미설정";
  $("#logoutButton").hidden = !state.accounts_enabled;
  if (state.user?.role !== "admin") setAdminView(false, false);
  const current = $("#packSelect").value || state.default_pack;
  $("#packSelect").replaceChildren(
    ...state.packs.map((p) => {
      const o = el("option", p.name + " · " + p.version);
      o.value = p.id;
      return o;
    }),
  );
  if (state.packs.some((p) => p.id === current))
    $("#packSelect").value = current;
  renderSidebar();
}
function renderSidebar() {
  for (const [selector, chats, close] of [
    ["#tabs", state.chats.filter((c) => c.open), true],
    ["#recent", state.chats, false],
  ]) {
    const target = $(selector);
    target.replaceChildren();
    if (!chats.length)
      target.append(
        el(
          "p",
          close ? "열린 탭이 없습니다" : "저장된 대화가 없습니다",
          "empty",
        ),
      );
    for (const c of chats) {
      const row = el(
        "div",
        undefined,
        "chatrow" + (c.id === active ? " active" : ""),
      );
      row.append(button(c.title, () => openChat(c.id)));
      const b = button(close ? "×" : "삭제", async () => {
        if (close) {
          await api("/api/chat/tab", { id: c.id, open: false });
          if (active === c.id) {
            navigation++;
            active = null;
            conversation = null;
            renderMessages();
          }
        } else {
          if (!confirm("대화를 삭제할까요? 전달 중인 문서가 있으면 삭제되지 않습니다. 검토·전달 사실(원문 제외)은 기록으로 남습니다."))
            return;
          await api("/api/chat/delete", { id: c.id });
          if (active === c.id) {
            navigation++;
            active = null;
            conversation = null;
            renderMessages();
          }
        }
        await refresh();
      });
      b.setAttribute(
        "aria-label",
        close ? "탭 닫기: " + c.title : "대화 삭제: " + c.title,
      );
      if (!close) b.className = "delete-chat";
      row.append(b);
      target.append(row);
    }
  }
}
async function newChat() {
  const c = await api("/api/chat", { pack_id: $("#packSelect").value });
  await openChat(c.id);
  $("#message").focus();
}
async function openChat(id) {
  const view = ++navigation;
  active = id;
  await api("/api/chat/tab", { id, open: true });
  if (view === navigation) await reloadChat(id, view);
}
async function reloadChat(id, view) {
  if (view !== navigation || active !== id) return;
  const loaded = await api("/api/chat/" + id);
  await refresh();
  if (view !== navigation || active !== id) return;
  conversation = loaded;
  $("#packSelect").value = conversation.chat.pack_id;
  renderMessages();
}
function renderMessages() {
  const target = $("#messages");
  target.replaceChildren();
  $("#chatTitle").textContent = conversation?.chat.title || "새 대화";
  $("#packSelect").disabled = !!active;
  if (!conversation?.turns.length) {
    const w = el("div", undefined, "welcome");
    w.append(
      el("div", "FIELD WORK ASSISTANT", "eyebrow"),
      el("h1", "현장의 질문에,\n확인 가능한 근거를."),
    );
    w.querySelector("h1").style.whiteSpace = "pre-line";
    w.append(
      el(
        "p",
        "상황을 남기면 매뉴얼의 관련 구절과 확인할 항목을 찾아드립니다.\n판단이 어려운 상황은 담당자 검토로 이어집니다.",
      ),
    );
    const grid = el("div", undefined, "examples");
    const p =
      state.packs.find((p) => p.id === $("#packSelect").value) ||
      state.packs[0];
    p?.examples.forEach((q, i) => {
      const b = button(
        "",
        () => {
          $("#message").value = q;
          $("#message").focus();
        },
        "example",
      );
      b.append(
        el(
          "small",
          [
            "01 / 점검 목록",
            "02 / 위험 상황",
            "03 / 근거 비교",
            "04 / 공개 정보",
          ][i] || "예시",
        ),
        el("span", q),
      );
      grid.append(b);
    });
    w.append(
      grid,
      el(
        "p",
        "가상·시연용 자료로 시작합니다. 실제 모델 연결 상태는 설정에서 확인하세요.",
        "availability",
      ),
    );
    target.append(w);
    return;
  }
  for (const t of conversation.turns) {
    target.append(el("div", t.text, "user-message"));
    const a = el("article", undefined, "answer");
    const head = el("div", undefined, "answerhead");
    head.append(
      el("b", "A", "mark"),
      el("span", "AlphaDrex"),
      el("span", t.decision.method, "badge"),
    );
    a.append(head);
    if (t.decision.emergency)
      a.append(
        el(
          "div",
          "긴급 상황 의심 · AI 처리를 기다리지 말고 기관 긴급 절차와 현장 지휘 체계를 우선하세요.",
          "status warning",
        ),
      );
    for (const al of (conversation.alerts || []).filter((x) => x.turn_id === t.id))
      a.append(el("div", "추가 경고 · " + al.message, "status warning"));
    if (t.decision.method === "로컬 규칙 시험 (AI 판단 아님)")
      a.append(
        el(
          "div",
          "AI 꺼짐 또는 응답 없음 · 키워드로 찾은 결과만 보여 줍니다 · 담당자가 직접 확인하세요" +
            (t.decision.error ? " (원인: " + t.decision.error + ")" : ""),
          "status ai-off",
        ),
      );
    if (t.decision.hybrid) {
      const h = t.decision.hybrid;
      const why = h.escalation_reasons.length ? " · 이유: " + h.escalation_reasons.join(", ") : "";
      const shadow = h.shadow ? " · Liner 비교 표본" : "";
      a.append(el("div", "판단 경로: " + h.route + why + shadow, "status tech"));
    }
    if (t.decision.risk_word_note)
      a.append(el("div", t.decision.risk_word_note, "status"));
    a.append(el("div", t.status + " · 분류: " + t.decision.category, "status"));
    if (t.conflict)
      a.append(
        el(
          "p",
          "문서 기준 또는 적용 버전의 검토가 필요합니다. 양쪽 문서를 확인하고 담당자가 적용 기준을 정하기 전에는 임의로 하나를 선택하지 마세요.",
        ),
      );
    if (!t.evidence.length)
      a.append(
        el(
          "p",
          "등록된 매뉴얼에서 충분한 근거를 찾지 못했습니다. 문서를 추가하거나 상황을 구체화해 주세요.",
        ),
      );
    else {
      a.append(el("h3", "매뉴얼에서 찾은 확인 근거"));
      const list = el("ul");
      for (const e of t.evidence.slice(0, 3)) {
        list.append(el("li", e.text));
      }
      a.append(
        list,
        el(
          "p",
          t.evidence.map((e) => e.title + " · " + e.location).join(" / "),
          "evidence-summary",
        ),
      );
    }
    if (t.decision.needs_info) {
      a.append(el("h3", "추가로 확인할 사항"));
      const list = el("ul");
      t.questions.forEach((q) => list.append(el("li", q)));
      a.append(list);
    }
    if (!t.decision.needs_info) a.append(
      el("h3", "보고 초안"),
      el("p", t.draft_status, "muted"),
      el("pre", t.draft?.text || t.template),
    );
    if (t.draft && !t.decision.needs_info)
      a.append(
        el(
          "small",
          `${t.draft.route} · 요청 ${t.draft.requested_model} / 응답 ${t.draft.returned_model}`,
          "tech",
        ),
      );
    if (t.search_query) {
      const box = el("div", undefined, "searchbox");
      const [label, input] = field(
        "외부로 보낼 검색어를 확인·수정하세요",
        "text",
        t.search_query,
      );
      input.maxLength = 200;
      box.append(
        label,
        el(
          "p",
          "이 검색어만 Search API로 전송됩니다. 개인정보·비공개 정보를 넣지 마세요. 검색 결과 원문은 별도 검토가 필요합니다.",
        ),
      );
      const b = button("검색어 확인 후 공개 검색", async () => {
        b.disabled = true;
        try {
          await api("/api/search", {
            turn_id: t.id,
            query: input.value,
            confirmed: true,
          });
          if (active === t.chat_id) await reloadChat(t.chat_id, navigation);
        } finally {
          b.disabled = false;
        }
      });
      box.append(b);
      if (state.policy.mode === "internal" || !state.policy.external_enabled) {
        b.disabled = true;
        box.append(
          el("p", "서버 정책으로 차단됨 · 외부망 및 외부 전송 설정 필요"),
        );
      }
      a.append(box);
    }
    for (const r of conversation.revisions.filter((r) => r.turn_id === t.id)) {
      const box = el("div", undefined, "source");
      box.append(
        el("strong", "후속 공개 검색 · " + r.status),
        el("small", r.query),
      );
      for (const result of r.results) {
        box.append(
          link(result.title, result.url),
          el("p", result.summary),
          el("small", result.date),
        );
      }
      if (r.draft) box.append(el("pre", r.draft.text));
      if (r.draft_error) box.append(el("p", r.draft_error));
      box.append(
        el("small", `검색 ${r.search_ms}ms · 호출 ${r.search_calls}회`),
      );
      a.append(box);
    }
    const actions = el("div", undefined, "answer-actions");
    actions.append(
      button("근거 " + t.evidence.length + "건 · 처리 경로", () =>
        showDetails(t.id),
      ),
      button("담당자 검토 기록", () => showDetails(t.id, true)),
      button("문서화 · 에이전트 전달", () => openHandoff(t)),
    );
    a.append(
      actions,
      el(
        "p",
        `처리 ${t.metrics.total_ms}ms · 로컬 검색 ${t.metrics.retrieval_ms}ms · 실제 API 비용 미확인`,
        "metricsline tech",
      ),
    );
    target.append(a);
  }
  target.scrollTop = target.scrollHeight;
}
function showDetails(id, review = false) {
  if (!conversation?.turns.length) {
    $("#details").hidden = false;
    $("#detailContent").replaceChildren(
      el("p", "대화를 시작하면 근거와 처리 경로가 표시됩니다."),
    );
    return;
  }
  selected =
    conversation.turns.find((t) => t.id === id) || conversation.turns.at(-1);
  $("#details").hidden = false;
  const target = $("#detailContent");
  target.replaceChildren();
  if (!selected) return;
  const t = selected;
  target.append(
    el(
      "p",
      "등록 매뉴얼과 공개 검색 결과는 별도 자료입니다. 일반 국민행동요령을 기관 내부 지침으로 사용하지 마세요.",
      "muted",
    ),
  );
  for (const e of t.evidence) {
    const card = el("div", undefined, "source");
    card.append(
      el("strong", e.title),
      el("span", e.kind, "badge"),
      el("small", `${e.publisher} · 버전 ${e.version} · ${e.location}`),
      el("small", "적용 대상: " + e.audience),
      el("blockquote", e.text),
      el("small", "찾은 방법: " + (e.match || "키워드")),
      el("small", "최신성: " + e.freshness),
    );
    if (e.url) card.append(link("원문 출처", e.url));
    target.append(card);
  }
  target.append(
    el("h3", "판단 경로와 실제 처리 (관리자 보기)", "sectiontitle tech"),
    el(
      "pre",
      JSON.stringify(
        { method: t.decision.method, decision: t.decision, metrics: t.metrics },
        null,
        2,
      ),
      "tech",
    ),
  );
  const form = el("form");
  form.id = "feedbackForm";
  form.append(
    el("h3", "담당자 검토", "sectiontitle"),
    el(
      "p",
      "검토 기록은 최초 결과와 별도 저장됩니다. 자동 학습이나 기준 변경을 수행하지 않습니다.",
      "muted",
    ),
  );
  const inputs = {};
  for (const [key, label] of [
    ["category_ok", "분류가 적절한가요?"],
    ["evidence_ok", "근거가 적절한가요?"],
    ["search_needed", "공개 검색이 필요한가요?"],
    ["risk_missed", "위험 검토를 누락했나요?"],
    ["draft_useful", "초안이 유용한가요?"],
  ]) {
    const [w, i] = select(
      label,
      [
        ["unknown", "미확인"],
        ["yes", "예"],
        ["no", "아니오"],
      ],
      "unknown",
    );
    inputs[key] = i;
    form.append(w);
  }
  const [cw, ci] = select(
    "올바른 업무 유형",
    t.pack_snapshot.categories.map((x) => [x, x]),
    t.decision.category,
  );
  inputs.correct_category = ci;
  form.append(cw);
  for (const [k, l, v] of [
    ["edited", "담당자가 수정한 결과", t.draft?.text || t.template],
    ["reason", "수정·검토 이유", ""],
  ]) {
    const [w, i] = field(l, "textarea", v);
    i.rows = k === "edited" ? 6 : 3;
    inputs[k] = i;
    form.append(w);
  }
  const save = el("button", "검토 결과 저장", "primary wide");
  save.type = "submit";
  form.append(save);
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const view = navigation;
    save.disabled = true;
    try {
      await api("/api/feedback", {
        turn_id: t.id,
        ...Object.fromEntries(
          Object.entries(inputs).map(([k, i]) => [k, i.value]),
        ),
      });
      await reloadChat(t.chat_id, view);
      if (view === navigation) showDetails(t.id, true);
    } catch (e) {
      showError(e);
    } finally {
      save.disabled = false;
    }
  });
  target.append(form);
  for (const f of conversation.feedback.filter((f) => f.turn_id === t.id)) {
    const card = el("div", undefined, "source");
    card.append(
      el(
        "strong",
        "저장된 검토 · " + new Date(f.created).toLocaleString("ko-KR") + (f.author ? " · " + f.author + (f.author_verified ? "" : " (미인증)") : ""),
      ),
      el("p", "수정 분류: " + f.correct_category),
      el("pre", f.edited),
      el("p", "이유: " + f.reason),
    );
    target.append(card);
  }
  if (review) form.scrollIntoView({ block: "start" });
}
const ADMIN_TABS = [
  "일반",
  "내 계정",
  "계정과 기록",
  "매뉴얼 관리",
  "업무 팩 관리",
  "데이터와 백업",
  "모델 연결",
  "성능과 테스트",
];
const STAFF_TABS = ["일반", "내 계정"];
const isAdmin = () => state?.user?.role === "admin";
const settingsError = (e) => {
  const box = $("#settingsError");
  box.textContent = e ? e.message || String(e) : "";
  box.hidden = !e;
};
async function openSettings(tab = "일반") {
  await refresh();
  const tabs = isAdmin() ? ADMIN_TABS : STAFF_TABS;
  settingTab = tabs.includes(tab) ? tab : "일반";
  settingsError(null);
  $("#settings").showModal();
  renderSettings();
}
function fileToBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(",", 2)[1] || "");
    reader.onerror = () => reject(Error("파일을 읽지 못했습니다"));
    reader.readAsDataURL(file);
  });
}
function renderSettings() {
  const tabs = isAdmin() ? ADMIN_TABS : STAFF_TABS;
  const nav = $("#settingNav");
  nav.replaceChildren(
    ...tabs.map((t) =>
      button(
        t,
        () => {
          settingTab = t;
          settingsError(null);
          renderSettings();
        },
        t === settingTab ? "active" : "",
      ),
    ),
  );
  const box = $("#settingContent");
  box.replaceChildren(el("h2", settingTab));
  const card = (title, text) => {
    const c = el("div", undefined, "card");
    c.append(el("h3", title), el("p", text));
    box.append(c);
    return c;
  };
  if (settingTab === "일반") {
    const [fw, large] = checkbox("글자 크게 보기", document.documentElement.classList.contains("large"));
    large.addEventListener("change", () => setLarge(large.checked));
    box.append(fw);
    if (isAdmin()) {
      const [aw, adminView] = checkbox(
        "관리자 보기 (처리 시간·모델 이름·판단 원자료 표시)",
        document.body.classList.contains("admin-view"),
      );
      adminView.addEventListener("change", () => setAdminView(adminView.checked));
      box.append(aw);
    }
    if (!state.accounts_enabled)
      card(
        "계정이 아직 없습니다",
        "지금은 이 PC를 쓰는 누구나 모든 기능을 쓸 수 있습니다. 실무에 쓰기 전에 [계정과 기록]에서 관리자 계정을 만드세요. 계정을 만들면 로그인한 사람만 쓸 수 있고, 누가 무엇을 했는지 기록됩니다.",
      );
    if (!isAdmin()) {
      card("AlphaDrex 현장 업무 지원", "버전 " + state.version + " · 설정 변경은 관리자에게 요청하세요.");
      return;
    }
    const [pw, defaultPack] = select(
      "기본 업무 팩",
      state.packs.map((p) => [p.id, p.name]),
      state.default_pack,
    );
    box.append(pw);
    card("AlphaDrex 현장 업무 지원", "버전 " + state.version + "\n저장 위치: " + state.storage);
    const [w, mode] = select(
      "연결 모드",
      [
        ["internal", "완전히 분리된 내부망"],
        ["external", "외부망"],
      ],
      state.policy.mode,
    );
    const [cw, enabled] = checkbox(
      "외부 서비스 전송 허용 (기관 승인 조건 별도 확인)",
      state.policy.external_enabled,
    );
    box.append(
      w,
      cw,
      el("p", "모드별 저장소는 분리됩니다. 자동 복사하지 않으며 재시작 시 내부망으로 돌아옵니다.", "muted"),
      button(
        "설정 적용",
        async () => {
          await api("/api/settings", {
            mode: mode.value,
            external_enabled: enabled.checked,
            default_pack: mode.value === state.policy.mode ? defaultPack.value : undefined,
          });
          navigation++;
          active = null;
          conversation = null;
          $("#details").hidden = true;
          $("#packSelect").value = "";
          await refresh();
          renderMessages();
          renderSettings();
        },
        "primary",
      ),
    );
  }
  if (settingTab === "내 계정") {
    if (!state.accounts_enabled) {
      card("계정 없음", "관리자 계정을 만들면 여기서 비밀번호를 바꿀 수 있습니다.");
      return;
    }
    card("로그인한 사람", state.user.name + " · " + (isAdmin() ? "관리자" : "근무자"));
    const [pw, pass] = field("새 비밀번호 (8자 이상)", "password");
    box.append(
      pw,
      button(
        "비밀번호 바꾸기",
        async () => {
          await api("/api/users/password", { name: state.user.name, password: pass.value });
          pass.value = "";
          card("완료", "비밀번호를 바꿨습니다. 다른 PC의 로그인은 끊어졌습니다.");
          await logout();
        },
        "primary",
      ),
      button("로그아웃", () => logout()),
    );
  }
  if (settingTab === "계정과 기록") {
    if (!state.accounts_enabled) {
      card(
        "첫 관리자 계정 만들기",
        "만든 뒤에는 로그인한 사람만 앱을 쓸 수 있습니다. 비밀번호를 잊으면 다른 관리자만 바꿀 수 있으니 관리자를 2명 이상 두세요.",
      );
      const [nw, name] = field("관리자 이름");
      const [pw, pass] = field("비밀번호 (8자 이상)", "password");
      box.append(
        nw,
        pw,
        button(
          "관리자 계정 만들기",
          async () => {
            await api("/api/setup", { name: name.value, password: pass.value });
            await refresh();
            renderSettings();
          },
          "primary",
        ),
      );
      return;
    }
    const usersBox = el("div");
    box.append(el("h3", "계정"), usersBox);
    api("/api/users")
      .then(({ users }) => {
        for (const u of users) {
          const row = el("div", undefined, "card");
          row.append(
            el("strong", u.name + " · " + (u.role === "admin" ? "관리자" : "근무자") + (u.disabled ? " · 사용 중지" : "")),
            button(u.disabled ? "다시 사용" : "사용 중지", async () => {
              await api("/api/users/disable", { name: u.name, disabled: !u.disabled });
              renderSettings();
            }),
          );
          if (u.name !== state.user.name) {
            const [rw, reset] = field("새 비밀번호로 재설정", "password");
            row.append(
              rw,
              button("비밀번호 재설정", async () => {
                await api("/api/users/password", { name: u.name, password: reset.value });
                reset.value = "";
                settingsError(Error(u.name + " 비밀번호를 재설정했습니다."));
              }),
            );
          }
          usersBox.append(row);
        }
      })
      .catch(settingsError);
    const [nw, name] = field("새 계정 이름");
    const [pw, pass] = field("처음 비밀번호 (8자 이상)", "password");
    const [rw, role] = select("권한", [["staff", "근무자"], ["admin", "관리자"]], "staff");
    box.append(
      nw,
      pw,
      rw,
      button(
        "계정 추가",
        async () => {
          await api("/api/users", { name: name.value, password: pass.value, role: role.value });
          renderSettings();
        },
        "primary",
      ),
    );
    const logBox = el("div");
    box.append(
      el("h3", "행위 기록 (최근 100건 · 지워지지 않음)"),
      el("p", "누가 언제 무엇을 했는지 남깁니다. 상황 원문과 비밀번호는 기록하지 않습니다.", "muted"),
      logBox,
    );
    api("/api/actions")
      .then(({ actions }) => {
        const table = el("table", undefined, "actions");
        const head = el("tr");
        ["시각", "사람", "행위", "대상", "결과"].forEach((h) => head.append(el("th", h)));
        table.append(head);
        for (const a of actions.slice(0, 100)) {
          const tr = el("tr");
          [
            new Date(a.created).toLocaleString("ko-KR"),
            a.actor + (a.verified ? "" : " (미인증)"),
            ACTION_NAMES[a.path] || a.path,
            Object.values(a.targets || {}).join(" ").slice(0, 40),
            a.outcome + (a.error ? " · " + a.error : ""),
          ].forEach((v) => tr.append(el("td", v)));
          table.append(tr);
        }
        const wrap = el("div", undefined, "tablewrap");
        wrap.append(table);
        logBox.append(
          wrap,
          button("행위 기록 CSV 내려받기", () => {
            const rows = [["시각", "사람", "인증", "권한", "행위", "대상", "결과", "오류"]].concat(
              actions.map((a) => [a.created, a.actor, a.verified ? "예" : "아니오", a.role, a.path,
                JSON.stringify(a.targets || {}), a.outcome, a.error || ""]),
            );
            const csv = "﻿" + rows.map((r) => r.map(csvCell).join(",")).join("\r\n");
            download("AlphaDrex-행위기록-" + state.policy.mode + ".csv", csv, "text/csv");
          }),
        );
      })
      .catch(settingsError);
  }
  if (settingTab === "매뉴얼 관리") {
    card(
      "문서 등록",
      "TXT·MD·HWPX·PDF를 등록할 수 있습니다. HWP(한글 기본 형식)는 한글에서 [다른 이름으로 저장] → HWPX로 저장해 올리세요. 스캔 이미지 PDF는 글자를 읽지 못합니다. 개정판은 [교체할 기존 문서]를 고르면 옛 판은 보관만 하고 검색에서 빠집니다.",
    );
    const current = state.manuals.filter((d) => !d.superseded_by);
    const archived = state.manuals.filter((d) => d.superseded_by);
    const form = el("form"),
      inputs = {};
    const [fw, file] = field("첨부할 문서", "file");
    file.accept = ".txt,.md,.hwpx,.pdf,.hwp";
    form.append(fw);
    const [repw, replaces] = select(
      "교체할 기존 문서 (개정판일 때만)",
      [["", "새 문서로 추가"], ...current.map((d) => [d.id, d.title + " · " + d.version])],
      "",
    );
    form.append(repw);
    for (const [k, l, v] of [
      ["title", "문서명", ""],
      ["version", "버전", ""],
      ["date", "시행·발행일", ""],
    ]) {
      const [w, i] = field(l, "text", v);
      inputs[k] = i;
      form.append(w);
    }
    for (const [k, l, options, v] of [
      ["kind", "자료 종류", ["내부 자료", "공개 자료", "가상·시연용"].map((x) => [x, x]), "내부 자료"],
      ["pack_id", "연결 업무 팩", state.packs.map((p) => [p.id, p.name]), $("#packSelect").value || state.default_pack],
    ]) {
      const [w, i] = select(l, options, v);
      inputs[k] = i;
      form.append(w);
    }
    const more = el("details");
    more.append(el("summary", "추가 정보 (선택)"));
    for (const [k, l] of [
      ["publisher", "발행기관"],
      ["audience", "적용 대상"],
      ["url", "원문 URL"],
      ["license", "사용·재배포 조건"],
      ["freshness", "최신성 확인"],
      ["conflict_group", "충돌 검토 그룹 (관련 규정에 같은 이름 부여)"],
    ]) {
      const [w, i] = field(l, "text", "");
      inputs[k] = i;
      more.append(w);
    }
    form.append(more);
    const [aw, approved] = checkbox("내용과 사용 조건을 검토하여 로컬 사용 승인", true);
    const [ew, external] = checkbox("공개·가상 자료의 외부 모델 전송 승인");
    form.append(aw, ew);
    const submit = el("button", "문서 등록", "primary");
    submit.type = "submit";
    form.append(submit);
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      settingsError(null);
      submit.disabled = true;
      try {
        const f = file.files[0];
        if (!f) throw Error("파일을 선택하세요");
        const lower = f.name.toLowerCase();
        const binary = lower.endsWith(".hwpx") || lower.endsWith(".pdf") || lower.endsWith(".hwp");
        if (binary && f.size > 8000000) throw Error("HWPX·PDF는 8MB 이하만 등록할 수 있습니다");
        if (!binary && f.size > 900000) throw Error("텍스트 파일은 900KB 이하만 등록할 수 있습니다");
        const values = Object.fromEntries(
          Object.entries(inputs).map(([k, i]) => [k, i.value.trim() || (["title"].includes(k) ? f.name : "미확인")]),
        );
        values.conflict_group = inputs.conflict_group.value.trim();
        values.url = inputs.url.value.trim();
        const doc = await api("/api/manual", {
          filename: f.name,
          ...(binary ? { data_base64: await fileToBase64(f) } : { text: await f.text() }),
          replaces: replaces.value || undefined,
          approved: approved.checked,
          external_approved: external.checked,
          ...values,
        });
        await refresh();
        renderSettings();
        settingsError(Error("등록 완료 · " + doc.title + " · " + (doc.extract_note || "")));
      } catch (e) {
        settingsError(e);
      } finally {
        submit.disabled = false;
      }
    });
    box.append(form, el("h3", "사용 중인 문서 " + current.length + "개"));
    for (const d of current) {
      const c = card(
        d.title,
        `${d.kind} · 버전 ${d.version} · ${d.date || "시행일 미확인"}\n사용 ${d.approved ? "승인" : "미승인"} / 외부 전송 ${d.external_approved ? "승인" : "미승인"}` +
          (d.replaces ? "\n개정판 (옛 판 보관 중)" : "") +
          (d.registered_by ? "\n등록: " + d.registered_by : ""),
      );
      c.append(
        button("내려받기", () => download(d.filename.replace(/\.(hwpx|pdf)$/i, ".txt"), d.text, "text/plain")),
        button(
          "삭제",
          async () => {
            if (confirm("이 문서를 삭제할까요? 기존 답변의 근거 기록은 남습니다. 개정판이 있으면 [교체]를 쓰세요.")) {
              await api("/api/manual/delete", { id: d.id });
              await refresh();
              renderSettings();
            }
          },
          "danger",
        ),
      );
    }
    if (archived.length) {
      box.append(el("h3", "보관 중인 옛 판 " + archived.length + "개 (검색 제외)"));
      for (const d of archived)
        card(d.title + " · " + d.version, "교체일 " + new Date(d.superseded).toLocaleString("ko-KR"));
    }
    for (const source of state.public_sources) {
      const c = card("공개 자료 후보 · " + source.title, source.content_status + " / 사용 조건: " + source.license);
      c.append(link("공식 원문 확인", source.url));
    }
  }
  if (settingTab === "업무 팩 관리") {
    for (const p of state.packs) {
      const c = card(p.name, p.description + " · " + p.version);
      const pre = el("pre", JSON.stringify(p, null, 2), "tech");
      c.append(pre, button("JSON 내보내기", () => download(p.id + ".json", p)));
    }
    const [w, i] = field("업무 팩 JSON 가져오기", "file");
    i.accept = ".json";
    box.append(
      w,
      button("가져오기 / 같은 ID 교체", async () => {
        if (!i.files[0]) throw Error("JSON 파일을 선택하세요");
        if (i.files[0].size > 100000) throw Error("파일이 너무 큽니다");
        const p = JSON.parse(await i.files[0].text());
        const overwrite = state.packs.some((x) => x.id === p.id);
        if (overwrite && !confirm("기존 문서 연결을 유지하고 업무 팩 설정을 교체할까요?")) return;
        await api("/api/pack", { pack: p, overwrite });
        await refresh();
        renderSettings();
      }),
    );
  }
  if (settingTab === "데이터와 백업") {
    card(
      "보안 적용 범위",
      (state.accounts_enabled ? "로그인 계정 사용 중 · 관리자만 이 화면을 봅니다. " : "계정 없음 · 누구나 접근 가능합니다. ") +
        "디스크 암호화와 망 분리 검증은 기관 절차를 따르세요. 개인정보 탐지는 일부 패턴만 지원하며 완전하지 않습니다.",
    );
    const backupBox = el("div");
    box.append(
      el("h3", "백업"),
      el("p", "서버가 켜질 때 하루 한 번 자동 백업합니다(최근 30개). 전체 삭제·복원 전에도 자동 백업합니다.", "muted"),
      button(
        "지금 백업",
        async () => {
          const r = await api("/api/backup", {});
          settingsError(Error("백업 완료 · " + r.backup));
          renderSettings();
        },
        "primary",
      ),
      backupBox,
    );
    api("/api/backups")
      .then(({ backups }) => {
        if (!backups.length) backupBox.append(el("p", "아직 백업이 없습니다."));
        const [sw, pick] = select(
          "복원할 백업",
          backups.map((b) => [b.name, new Date(b.created).toLocaleString("ko-KR") + " · " + BACKUP_LABELS[b.label] + " · " + Math.round(b.bytes / 1024) + "KB"]),
          backups[0]?.name,
        );
        const [cw, confirmText] = field("복원하려면 “백업으로 복원” 입력");
        if (backups.length)
          backupBox.append(
            sw,
            el("p", "복원하면 지금 데이터는 백업 시점으로 돌아갑니다. 지금 상태는 복원 직전에 따로 백업되고, 행위 기록은 지워지지 않습니다.", "muted"),
            cw,
            button(
              "선택한 백업으로 복원",
              async () => {
                const r = await api("/api/restore", { name: pick.value, confirm: confirmText.value });
                navigation++;
                active = null;
                conversation = null;
                await refresh();
                renderMessages();
                renderSettings();
                settingsError(Error("복원 완료 · 복원 전 상태는 " + r.before_restore_backup + "에 보관"));
              },
              "danger",
            ),
          );
      })
      .catch(settingsError);
    box.append(
      el("h3", "내보내기와 삭제"),
      button("현재 망 데이터 내보내기 (JSON)", async () =>
        download("AlphaDrex-" + state.policy.mode + ".json", await api("/api/export")),
      ),
    );
    const [w, i] = field("삭제하려면 “현재 망 데이터 삭제” 입력");
    box.append(
      w,
      button(
        "대화·첨부·검토 데이터 삭제 (자동 백업 후)",
        async () => {
          const r = await api("/api/data/delete", { confirm: i.value });
          navigation++;
          active = null;
          conversation = null;
          await refresh();
          renderMessages();
          renderSettings();
          settingsError(Error("삭제 완료 · 삭제 전 백업: " + r.backup));
        },
        "danger",
      ),
    );
  }
  if (settingTab === "모델 연결") {
    const c = state.connections;
    card(
      "Drex (이 PC의 AI)",
      `${c.drex_model}\n${c.drex_url}\n상태: ${c.drex_live === true ? "응답함" : c.drex_live === false ? "꺼짐" : "원격 주소 · 미확인"}`,
    );
    box.append(button("Drex 가상 입력으로 연결 시험", () => connectionTest("drex")));
    card(
      "Liner (외부 AI · 유료)",
      `요청 모델: ${c.liner_requested_model}\nAPI 키: ${c.liner_key_registered ? "등록됨 (브라우저에 주지 않음)" : "미등록 · env.local의 LINER_API_KEY 설정 후 재시작"}\n외부망 모드에서 공개·가상 입력일 때만 호출합니다. 검색은 대화에서 검색어를 확인한 경우에만 호출합니다.`,
    );
    box.append(button("Liner 가상 입력으로 연결 시험 (호출 1회)", () => connectionTest("liner")));
    card("MCP", "연결 기능 없음 (미구현).");
    for (const [k, v] of Object.entries(c.last_tests))
      box.append(el("h3", k + " 마지막 시험"), el("pre", JSON.stringify(v, null, 2), "tech"));
  }
  if (settingTab === "성능과 테스트") {
    card(
      "실측과 목표 구분",
      "70% 절감, 1초 응답, 183ms는 달성 성과가 아닙니다. 아래 시간은 해당 실행의 측정값입니다. 규칙 시험과 실제 Drex 추론을 구분하세요. 분류 정확도는 담당자 검토 사례만 집계합니다.",
    );
    box.append(el("pre", JSON.stringify(state.metrics, null, 2)));
    card("자동 테스트", "전체 테스트와 검토 기록은 evaluation/RESULTS.md, docs/REVIEW_DEBATE.md에서 확인할 수 있습니다. 실제 청구 비용은 미확인입니다.");
  }
}
const ACTION_NAMES = {
  "/api/login": "로그인", "/api/logout": "로그아웃", "/api/setup": "첫 관리자 생성", "/api/chat": "새 대화",
  "/api/chat/tab": "탭 열기·닫기", "/api/chat/delete": "대화 삭제", "/api/message": "상황 입력", "/api/preview": "미리보기",
  "/api/search": "공개 검색", "/api/feedback": "담당자 검토 저장", "/api/manual": "매뉴얼 등록", "/api/manual/delete": "매뉴얼 삭제",
  "/api/pack": "업무 팩 변경", "/api/settings": "망·설정 변경", "/api/data/delete": "전체 데이터 삭제", "/api/backup": "백업",
  "/api/restore": "백업 복원", "/api/users": "계정 추가", "/api/users/disable": "계정 사용 중지·재사용",
  "/api/users/password": "비밀번호 변경", "/api/test": "연결 시험", "/api/export": "데이터 내보내기",
  "/api/report/create": "문서 작성", "/api/report/review": "문서 검토", "/api/handoff/prepare": "전달 준비",
  "/api/handoff/send": "전달 실행", "/api/agent/claim": "에이전트 접수", "/api/agent/reply": "에이전트 회신",
};
const BACKUP_LABELS = { daily: "자동(매일)", manual: "수동", "before-clear": "삭제 전 자동", "before-restore": "복원 전 자동" };
async function connectionTest(provider) {
  const result = await api("/api/test", { provider });
  await refresh();
  renderSettings();
  settingsError(Error((result.status.includes("성공") ? "연결 시험 성공 · " : "연결 시험 실패 · ") + result.status));
}
$("#composer").addEventListener("submit", async (e) => {
  e.preventDefault();
  if (busy) return;
  clearError();
  busy = true;
  $("#send").disabled = true;
  const text = $("#message").value;
  let pending, timer;
  try {
    if (!active) await newChat();
    const id = active;
    const view = navigation;
    pending = el(
      "div",
      "처리 중입니다. 긴급 상황이면 기관 절차를 먼저 따르세요.",
      "busy",
    );
    const elapsed = el("p", "", "elapsed");
    pending.append(elapsed);
    const began = Date.now();
    const tick = () => {
      const sec = Math.round((Date.now() - began) / 1000);
      elapsed.textContent =
        "경과 " + sec + "초 · AI 판단은 보통 30~60초, 최대 2분 걸립니다" +
        (sec >= 60 ? " · 오래 걸리면 아래 매뉴얼 구절을 먼저 확인하세요" : "");
    };
    tick();
    timer = setInterval(tick, 1000);
    $("#messages").append(pending);
    // Local keyword hits appear at once; the model's judgment (tens of seconds on CPU) replaces them.
    api("/api/preview", { chat_id: id, text })
      .then((p) => {
        if (!pending.isConnected) return;
        if (p.emergency_rule)
          pending.append(
            el("p", "위험 표현 감지: 응답을 기다리지 말고 기관 긴급 절차를 먼저 시작하세요.", "preview-urgent"),
          );
        pending.append(el("p", p.note, "preview-note"));
        if (!p.evidence.length)
          pending.append(el("p", "키워드로 찾은 매뉴얼 구절이 아직 없습니다. AI 판단을 기다리는 중입니다."));
        for (const e of p.evidence) {
          pending.append(el("p", e.text, "preview-line"));
          pending.append(el("small", e.title + " · " + e.location));
        }
        pending.scrollIntoView({ block: "end" });
      })
      .catch(() => {});
    await api("/api/message", {
      chat_id: id,
      text,
      allow_external: $("#publicInput").checked,
    });
    if (view === navigation && $("#message").value === text) $("#message").value = "";
    if (active === id) await reloadChat(id, navigation);
    else await refresh();
    watchLateWarning(id);
  } catch (e) {
    showError(e);
  } finally {
    clearInterval(timer);
    pending?.remove();
    busy = false;
    $("#send").disabled = false;
  }
});
// When the wait cap handed a turn to the keyword rules, Drex may still answer later. A late "emergency"
// is saved as a separate warning; check a few times so the person sees it without reloading.
function watchLateWarning(id) {
  const last = conversation?.turns.at(-1);
  if (!last || !String(last.decision.error || "").includes("초과")) return;
  let tries = 0;
  const seen = (conversation.alerts || []).length;
  const poll = setInterval(async () => {
    tries += 1;
    if (tries > 12 || active !== id || busy) return clearInterval(poll);
    try {
      const loaded = await api("/api/chat/" + id);
      if ((loaded.alerts || []).length > seen && active === id) {
        clearInterval(poll);
        conversation = loaded;
        renderMessages();
      }
    } catch {}
  }, 15000);
}
$("#message").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && (e.ctrlKey || e.metaKey))
    $("#composer").requestSubmit();
});
action($("#newChat"), async () => {
  if (busy) throw Error("현재 처리가 끝난 뒤 새 대화를 시작하세요");
  await newChat();
});
action($("#settingsButton"), () => openSettings());
action($("#attach"), () => openSettings("매뉴얼 관리"));
action($("#detailButton"), () => showDetails(conversation?.turns.at(-1)?.id));
$("#closeDetails").onclick = () => ($("#details").hidden = true);
$("#closeSettings").onclick = () => $("#settings").close();
$("#collapse").onclick = () =>
  document.body.classList.toggle(
    innerWidth <= 760 ? "mobile-open" : "collapsed",
  );
$("#packSelect").onchange = () => {
  if (!active) renderMessages();
};
function store_(key, value) {
  try {
    if (value === undefined) return localStorage.getItem(key);
    localStorage.setItem(key, value);
  } catch {}
  return null;
}
function setLarge(on) {
  document.documentElement.classList.toggle("large", on);
  store_("alphadrex.large", on ? "1" : "0");
}
function setAdminView(on, remember = true) {
  document.body.classList.toggle("admin-view", on);
  if (remember) store_("alphadrex.adminView", on ? "1" : "0");
}
function showLogin() {
  const dlg = $("#loginDialog");
  if (!dlg.open) {
    resetAccountView();
    dlg.showModal();
  }
  $("#loginName").focus();
}
function resetAccountView() {
  authEpoch++;
  refreshEpoch++;
  navigation++;
  active = conversation = selected = state = null;
  settingTab = "일반";
  for (const id of ["settings", "handoffPanel"]) $("#" + id).close();
  for (const id of ["messages", "tabs", "recent", "packSelect", "settingContent", "settingNav", "detailContent", "handoffContent"])
    $("#" + id).replaceChildren();
  $("#details").hidden = true;
  $("#message").value = "";
  $("#publicInput").checked = false;
  $("#packSelect").disabled = false;
  $("#chatTitle").textContent = "새 대화";
  $("#userBadge").textContent = "";
  $("#logoutButton").hidden = true;
  clearError();
  settingsError(null);
  setAdminView(false, false);
  if (typeof resetHandoff === "function") resetHandoff();
}
function csvCell(value) {
  let text = String(value);
  if (/^[\s]*[=+@-]/.test(text) || /^[\t\r\n]/.test(text)) text = "'" + text;
  return '"' + text.replaceAll('"', '""') + '"';
}
async function logout() {
  await api("/api/logout", {});
  location.reload();
}
$("#loginForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  $("#loginError").hidden = true;
  try {
    await api("/api/login", { name: $("#loginName").value, password: $("#loginPassword").value });
    $("#loginPassword").value = "";
    $("#loginDialog").close();
    await refresh();
    renderMessages();
  } catch (err) {
    $("#loginError").textContent = err.message;
    $("#loginError").hidden = false;
  }
});
$("#loginDialog").addEventListener("cancel", (e) => e.preventDefault());
action($("#logoutButton"), () => logout());
action($("#fontButton"), () => setLarge(!document.documentElement.classList.contains("large")));
(async () => {
  setLarge(store_("alphadrex.large") === "1");
  try {
    const auth = await api("/api/auth");
    if (auth.accounts_enabled && !auth.user) return showLogin();
    setAdminView(store_("alphadrex.adminView") === "1");
    await refresh();
    renderMessages();
  } catch (e) {
    showError(e);
  }
})();
