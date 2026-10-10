const app = document.querySelector("#app");
const esc = (value) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (ch) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        ch
      ],
  );
const strengthTypes = ["A", "B", "C", "胸＋三头", "背＋二头", "腿", "肩＋核心"];
const trainingTitle = (type) =>
  ["A", "B", "C"].includes(type)
    ? `全身力量 ${type}`
    : type === "有氧"
      ? "中等强度有氧"
      : ["恢复", "休息"].includes(type)
        ? "恢复与休息"
        : `四分化 · ${type}`;
const fmt = (n, d = 1) => (typeof n === "number" ? n.toFixed(d) : "—");
const plusDays = (iso, n) => {
  const d = new Date(`${iso}T12:00:00Z`);
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
};
const today = () =>
  new Intl.DateTimeFormat("en-CA", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(new Date());
const daysBetween = (a, b) =>
  Math.round(
    (new Date(`${a}T12:00:00Z`) - new Date(`${b}T12:00:00Z`)) / 86400000,
  );
let state,
  selected,
  tab = "today",
  saving = 0,
  saveChain = Promise.resolve(),
  toastTimer;
function toast(message) {
  const el = document.querySelector("#toast");
  el.textContent = message;
  el.classList.add("visible");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove("visible"), 5000);
}
async function api(path, options = {}) {
  const headers = {
    ...(options.body ? { "Content-Type": "application/json" } : {}),
    ...(state?.csrf ? { "X-CSRF-Token": state.csrf } : {}),
    ...options.headers,
  };
  const response = await fetch(path, {
    ...options,
    headers,
    credentials: "same-origin",
    cache: "no-store",
  });
  const payload = await response.json();
  if (!response.ok) {
    const error = new Error(payload.error || "请求失败，请重试");
    error.status = response.status;
    error.payload = payload;
    throw error;
  }
  return payload;
}
function login() {
  state = null;
  app.innerHTML = `<main class="login-page"><div class="login-wrap"><div class="login-intro"><img src="/icon.svg" alt=""><div class="eyebrow muted">90 DAY PLAN</div><h1>健身打卡</h1><p class="muted">今天吃什么、练什么，<br>完成后在这里记下来。</p></div><form id="login-form" class="card login-card"><label class="field">用户名<input name="username" autocomplete="username" required maxlength="32"></label><label class="field">密码<input name="password" type="password" autocomplete="current-password" required maxlength="256"></label><button class="button" type="submit">登录我的记录</button><p id="login-error" class="form-error" role="alert"></p></form><p class="login-foot">个人记录保存在你的服务器。<br>首次使用需先在服务器终端创建账户。</p></div></main>`;
  document
    .querySelector("#login-form")
    .addEventListener("submit", async (e) => {
      e.preventDefault();
      const form = e.currentTarget,
        button = form.querySelector("button");
      button.disabled = true;
      try {
        await api("/api/login", {
          method: "POST",
          body: JSON.stringify(Object.fromEntries(new FormData(form))),
        });
        await load();
      } catch (error) {
        document.querySelector("#login-error").textContent = error.message;
      } finally {
        button.disabled = false;
      }
    });
}
async function load() {
  try {
    state = await api("/api/state");
    const first = state.profile.startDate,
      last = plusDays(first, 89);
    selected =
      selected && selected >= first && selected <= last
        ? selected
        : today() < first
          ? first
          : today() > last
            ? last
            : today();
    render();
  } catch (error) {
    if (error.status === 401) login();
    else {
      app.innerHTML =
        '<main class="login-page"><div class="card"><h2>暂时无法读取计划</h2><p class="muted" id="load-error"></p><button id="retry" class="button">重新加载</button></div></main>';
      document.querySelector("#load-error").textContent = error.message;
      document.querySelector("#retry").onclick = load;
    }
  }
}
function planDay() {
  const p = state.plan.days[daysBetween(selected, state.profile.startDate)];
  return {
    ...p,
    steps: state.profile.stepGoal ?? "未设定",
    nutrition: {
      ...p.nutrition,
      kcal: state.profile.kcalGoal ?? "—",
      protein: state.profile.proteinGoal ?? "—",
    },
  };
}
function record(iso = selected) {
  return state.records[iso]?.data || {};
}
function countDone(d) {
  return (
    ["breakfast", "lunch", "dinner", "snack"].filter((k) => d[k] === "完成")
      .length + (["完成", "计划休息"].includes(d.workout) ? 1 : 0)
  );
}
function statusIndicator() {
  const el = document.querySelector("#save-state");
  if (el)
    el.textContent = saving
      ? "正在保存…"
      : navigator.onLine
        ? "已连接服务器"
        : "离线，修改暂不能保存";
}
function save(patch, renderAfter = true) {
  const day = selected;
  saving++;
  statusIndicator();
  saveChain = saveChain
    .catch(() => {})
    .then(async () => {
      try {
        const resolved =
          typeof patch === "function"
            ? patch(state.records[day]?.data || {})
            : patch;
        const response = await api(`/api/records/${day}`, {
          method: "PATCH",
          body: JSON.stringify({
            patch: resolved,
            revision: state.records[day]?.revision || 0,
          }),
        });
        state.records[day] = response.record;
        if (selected === day && tab === "today") syncToday();
        else if (renderAfter) render();
        return true;
      } catch (error) {
        if (error.status === 409) {
          state.records[day] = error.payload.record;
          if (selected === day && tab === "today") syncToday();
        }
        if (error.status === 401) {
          login();
          toast("登录已过期，请重新登录后保存。");
        } else
          toast(
            error.message +
              (error.status === 409 ? "" : "。本次修改未保存，请重新提交。"),
          );
        return false;
      } finally {
        saving--;
        statusIndicator();
      }
    });
  return saveChain;
}
function syncToday() {
  const d = record(),
    done = countDone(d);
  document.querySelector(".hero h2").textContent =
    done === 5
      ? "今天的五项安排已完成"
      : `已完成 ${done} 项，还有 ${5 - done} 项`;
  document
    .querySelector(".hero-meter svg circle:last-child")
    .setAttribute("stroke-dasharray", `${(done / 5) * 257.61} 257.61`);
  document.querySelector(".meter-label strong").innerHTML =
    `${done}<span> / 5</span>`;
  document.querySelectorAll("[data-complete]").forEach((el) => {
    const checked = d[el.dataset.complete] === "完成";
    el.textContent = checked ? "已完成 ✓" : "完成这餐";
    el.classList.toggle("checked", checked);
    el.setAttribute("aria-pressed", checked);
    el.closest(".meal-card").classList.toggle("complete", checked);
  });
  document.querySelectorAll("[data-status]").forEach((el) => {
    if (document.activeElement !== el) el.value = d[el.dataset.status] || "";
  });
  document.querySelectorAll("[data-action-done]").forEach((el) => {
    if (!el.disabled) el.checked = !!d.actions?.[el.dataset.actionDone]?.done;
    el.closest(".exercise").classList.toggle(
      "complete",
      !!d.actions?.[el.dataset.actionDone]?.done,
    );
  });
  const w = document.querySelector("#complete-workout"),
    checked = ["完成", "计划休息"].includes(d.workout);
  w.classList.toggle("checked", checked);
  w.textContent = checked
    ? "已记录 ✓"
    : ["恢复", "休息"].includes(planDay().trainingType)
      ? "记录计划休息"
      : "完成训练";
}
function shell(content) {
  return `<div class="shell"><header class="topbar"><div class="brand"><img src="/icon.svg" alt="">健身打卡</div><div class="online"><span class="dot"></span><span id="save-state">已连接服务器</span></div></header>${content}</div><nav class="tabbar" aria-label="主要导航"><div class="tabbar-inner">${[
    ["today", "每日计划"],
    ["progress", "进度"],
    ["analysis", "模型复盘"],
    ["settings", "设置"],
  ]
    .map(
      ([k, n]) =>
        `<button class="tab ${tab === k ? "active" : ""}" data-tab="${k}" ${tab === k ? 'aria-current="page"' : ""}>${n}</button>`,
    )
    .join("")}</div></nav>`;
}
function statusSelect(key, value) {
  return `<select class="state-select" data-status="${key}" aria-label="${esc(key === "workout" ? "训练" : { breakfast: "早餐", lunch: "午餐", dinner: "晚餐", snack: "加餐" }[key])}打卡状态"><option value="">未记录</option>${["完成", "部分", "未完成", ...(key === "workout" && ["恢复", "休息"].includes(planDay().trainingType) ? ["计划休息"] : [])].map((s) => `<option ${value === s ? "selected" : ""}>${s}</option>`).join("")}</select>`;
}
function todayPage() {
  const p = planDay(),
    d = record(),
    done = countDone(d);
  const label = new Date(`${selected}T12:00:00Z`).toLocaleDateString("zh-CN", {
    month: "long",
    day: "numeric",
    weekday: "long",
    timeZone: "Asia/Shanghai",
  });
  return `<div class="welcome"><div><h1>每日计划</h1><p class="muted">${esc(label)} · 第${p.week}周</p></div><span class="tag">第 ${p.day} / 90 天</span></div><div class="datebar"><div class="datecontrol"><button class="icon-button" id="prev-day" aria-label="前一天" ${p.day === 1 ? "disabled" : ""}>‹</button><input id="day-picker" aria-label="查看日期" type="date" value="${selected}" min="${state.profile.startDate}" max="${plusDays(state.profile.startDate, 89)}"><button class="icon-button" id="next-day" aria-label="后一天" ${p.day === 90 ? "disabled" : ""}>›</button></div><button id="today-button" class="button small secondary">回到今天</button></div>${selected > today() ? '<div class="notice">这是未来日期的安排，可以先查看。开始执行后再打卡。</div>' : ""}<section class="hero"><div><span class="eyebrow">TODAY’S CHECK-IN</span><h2>${done === 5 ? "今天的五项安排已完成" : `已完成 ${done} 项，还有 ${5 - done} 项`}</h2><p>四次餐食与一次训练分别记录。<br>加餐按需，实际食物和恢复可另行记录。</p></div><div class="hero-meter"><svg viewBox="0 0 100 100" aria-hidden="true"><circle cx="50" cy="50" r="41" fill="none" stroke="#345e4f" stroke-width="7"/><circle cx="50" cy="50" r="41" fill="none" stroke="#d2f28e" stroke-width="7" stroke-linecap="round" stroke-dasharray="${(done / 5) * 257.61} 257.61"/></svg><div class="meter-label"><strong>${done}<span> / 5</span></strong><span>今日打卡</span></div></div></section><div class="stats"><div class="stat"><label>计划能量</label><strong>${p.nutrition.kcal}</strong><small>千卡</small></div><div class="stat"><label>蛋白质</label><strong>${p.nutrition.protein}</strong><small>g</small></div><div class="stat"><label>步数目标</label><strong>${p.steps}</strong></div><div class="stat"><label>活动预算</label><strong>${p.minutes}</strong><small>分钟</small></div></div><div class="section-title"><h2>今天吃什么</h2><p>实际吃了什么，填写后自动保存</p></div><section class="meal-grid">${p.meals.map((m) => `<article class="card meal-card ${d[m.key] === "完成" ? "complete" : ""}"><div><div class="meal-label">${m.label}</div><h3>${esc(m.name)}</h3></div><p class="meal-detail">${esc(m.detail)}</p><label class="field">实际食物与份量<textarea data-meal="${m.key}" rows="2" maxlength="500" placeholder="未记录，例如：一碗米饭，炒青菜一盘">${esc(d.mealsActual?.[m.key] || "")}</textarea></label><div class="meal-controls"><button class="check-button ${d[m.key] === "完成" ? "checked" : ""}" data-complete="${m.key}" aria-pressed="${d[m.key] === "完成"}">${d[m.key] === "完成" ? "已完成 ✓" : "完成这餐"}</button>${statusSelect(m.key, d[m.key])}</div></article>`).join("")}</section><p class="note-line">${esc(state.plan.note)} 实际食物需另填，营养估算与实测分开。</p><div class="section-title"><h2>今天练什么</h2><p>通用示例，按恢复情况调整</p></div><section class="card"><div class="training-head"><div><h3>${esc(trainingTitle(p.trainingType))}</h3><p class="muted">${p.minutes}分钟左右 · 第${p.week}周安排</p></div><button id="complete-workout" class="check-button ${["完成", "计划休息"].includes(d.workout) ? "checked" : ""}">${["完成", "计划休息"].includes(d.workout) ? "已记录 ✓" : ["恢复", "休息"].includes(p.trainingType) ? "记录计划休息" : "完成训练"}</button></div>${p.actions
    .map((a) => {
      const actual = d.actions?.[a.id] || {};
      return `<article class="exercise ${actual.done ? "complete" : ""}"><div class="exercise-head"><input type="checkbox" data-action-done="${a.id}" ${actual.done ? "checked" : ""} aria-label="完成${esc(a.name)}"><div class="exercise-main"><div class="exercise-title"><h3>${esc(a.name)}</h3><span class="tag">约${a.minutes}分钟</span></div><p class="exercise-spec">${a.sets}组 × ${esc(a.reps)}${a.rest ? ` · 组间休息${a.rest}秒` : ""}</p><p class="exercise-tip">${esc(a.tip)}<br>替换：${esc(a.alternative)}</p>${a.rest ? `<div class="exercise-inputs"><label>实际重量 kg<input type="number" inputmode="decimal" min="0" max="400" step="0.5" data-action-weight="${a.id}" value="${actual.weight ?? ""}" placeholder="选合适重量"></label><label>实际工作组数<input type="number" inputmode="numeric" min="0" max="30" step="1" data-action-sets="${a.id}" value="${actual.sets ?? ""}" placeholder="未记录"></label><label>各组实际次数<input data-action-reps="${a.id}" maxlength="100" value="${esc(actual.reps || "")}" placeholder="例如 10 / 10 / 9"></label></div>` : ""}</div></div></article>`;
    })
    .join(
      "",
    )}<div class="row"><span class="small muted">训练状态</span>${statusSelect("workout", d.workout)}</div></section><p class="note-line">通用四分化模板：胸＋三头、背＋二头、有氧、腿、肩＋核心、有氧、休息。请按经验调整；有疼痛或明显不适时停止相关动作。</p><div class="section-title"><h2>身体与生活记录</h2><p>填写后自动保存</p></div><section class="card"><div class="metrics-grid">${[
    ["weight", "晨重 kg", "40", "150", "0.1"],
    ["waist", "腰围 cm", "40", "200", "0.1"],
    ["steps", "全天步数", "0", "60000", "1"],
    ["sleep", "睡眠 小时", "0", "24", "0.5"],
    ["cardioMinutes", "有氧 分钟", "0", "1440", "1"],
    ["cardioDistance", "有氧 公里", "0", "200", "0.1"],
  ]
    .map(
      ([key, name, min, max, step]) =>
        `<label class="field">${name}<input type="number" inputmode="decimal" min="${min}" max="${max}" step="${step}" data-metric="${key}" value="${d[key] ?? ""}" placeholder="未记录"></label>`,
    )
    .join(
      "",
    )}<label class="field wide">疲劳和恢复<select data-metric="fatigue"><option value="">未记录</option>${["轻松", "正常", "明显疲劳", "疼痛／不适"].map((s) => `<option ${d.fatigue === s ? "selected" : ""}>${s}</option>`).join("")}</select></label><label class="field notes">备注／额外食物<textarea data-metric="notes" rows="3" maxlength="2000" placeholder="例如：午餐换成牛肉；聚餐；今天睡得少">${esc(d.notes || "")}</textarea></label></div><p class="save-hint">尽量在一致条件下记录晨重与腰围，结合多日变化观察。记录来自手动输入，无 Apple 健康读取。</p></section>`;
}
function weeks() {
  return Array.from({ length: 13 }, (_, i) => {
    const start = plusDays(state.profile.startDate, i * 7),
      length = Math.min(7, 90 - i * 7),
      list = Array.from({ length }, (_, j) => ({
        date: plusDays(start, j),
        record: record(plusDays(start, j)),
        plan: state.plan.days[i * 7 + j],
      }));
    const weights = list
      .map((x) => x.record.weight)
      .filter((x) => typeof x === "number");
    const sleeps = list
      .map((x) => x.record.sleep)
      .filter((x) => typeof x === "number");
    const recorded = list.filter((x) =>
      Object.values(x.record).some((v) => v !== null && v !== ""),
    );
    return {
      week: i + 1,
      start,
      length,
      weights: weights.length,
      average: weights.length
        ? weights.reduce((a, b) => a + b, 0) / weights.length
        : null,
      sleep: sleeps.length
        ? sleeps.reduce((a, b) => a + b, 0) / sleeps.length
        : null,
      logged: recorded.length,
      full: recorded.filter((x) => countDone(x.record) === 5).length,
      strength: list.filter(
        (x) =>
          strengthTypes.includes(x.plan.trainingType) &&
          x.record.workout === "完成",
      ).length,
      cardio: list.filter(
        (x) => x.plan.trainingType === "有氧" && x.record.workout === "完成",
      ).length,
      target:
        typeof state.profile.startWeight === "number" &&
        typeof state.profile.targetWeight === "number"
          ? state.profile.startWeight -
            ((state.profile.startWeight - state.profile.targetWeight) *
              Math.min((i + 1) * 7, 90)) /
              90
          : null,
    };
  });
}
function chart(ws) {
  const nums = [
    state.profile.startWeight,
    state.profile.targetWeight,
    ...ws.map((w) => w.average),
  ].filter((v) => typeof v === "number");
  if (!nums.length) return '<p class="note-line">尚无晨重记录或体重目标。</p>';
  const high = Math.ceil(Math.max(...nums)) + 1,
    low = Math.floor(Math.min(...nums)) - 1;
  const x = (i) => 48 + i * (584 / 12),
    y = (v) => 215 - ((v - low) / (high - low)) * 175;
  const points = ws
    .filter((w) => w.average !== null)
    .map((w) => `${x(w.week - 1)},${y(w.average)}`);
  const grids = Array.from({ length: 5 }, (_, i) => {
    const value = low + ((high - low) * i) / 4;
    return `<line class="chart-grid" x1="48" x2="632" y1="${y(value)}" y2="${y(value)}"/><text class="chart-label" x="8" y="${y(value) + 4}">${value.toFixed(1)}</text>`;
  }).join("");
  return `<div class="chart-wrap"><svg viewBox="0 0 664 260" role="img" aria-label="13周平均体重与目标参考，单位公斤">${grids}${ws[0].target !== null ? `<polyline class="chart-target" points="${ws.map((w, i) => `${x(i)},${y(w.target)}`).join(" ")}"/>` : ""}${
    points.length
      ? `<polyline class="chart-actual" points="${points.join(" ")}"/>${ws
          .filter((w) => w.average !== null)
          .map(
            (w) =>
              `<circle cx="${x(w.week - 1)}" cy="${y(w.average)}" r="4" fill="#123f38"/>`,
          )
          .join("")}`
      : ""
  }${ws.map((w, i) => `<text class="chart-label" x="${x(i)}" y="243" text-anchor="middle">${w.week}</text>`).join("")}<text class="chart-label" x="650" y="243">周</text></svg></div>`;
}
function progressPage() {
  const ws = weeks(),
    all = Object.entries(state.records)
      .filter(
        ([iso]) =>
          daysBetween(iso, state.profile.startDate) >= 0 &&
          daysBetween(iso, state.profile.startDate) < 90,
      )
      .sort(([a], [b]) => a.localeCompare(b));
  const latest = all
    .filter(([, r]) => typeof r.data.weight === "number")
    .at(-1);
  const logged = all.filter(([, r]) =>
    Object.values(r.data).some((v) => v !== null && v !== ""),
  ).length;
  const completed = all.filter(([, r]) => countDone(r.data) === 5).length;
  return `<div class="welcome"><div><h1>进度</h1><p class="muted">看每周平均值，结合腰围和恢复情况。</p></div></div><div class="stats"><div class="stat"><label>最近晨重</label><strong>${latest ? fmt(latest[1].data.weight) : "—"}</strong><small>kg</small></div><div class="stat"><label>起始至今变化</label><strong>${latest && typeof state.profile.startWeight === "number" ? fmt(latest[1].data.weight - state.profile.startWeight) : "—"}</strong><small>kg</small></div><div class="stat"><label>记录天数</label><strong>${logged}</strong><small>/90</small></div><div class="stat"><label>五项完成</label><strong>${completed}</strong><small>天</small></div></div><section class="card"><h2>每周体重</h2><p class="muted small">起始${fmt(state.profile.startWeight)}kg，目标${fmt(state.profile.targetWeight)}kg。虚线仅作用户目标参考。</p>${chart(ws)}<div class="legend"><span>实际周均重</span><span class="target">目标参考</span></div>${!latest ? '<p class="note-line">记录晨重后，实际体重曲线会出现在这里。</p>' : ""}<div class="table-scroll"><table><thead><tr><th>周</th><th>日期</th><th>均重 kg</th><th>称重次数</th><th>较上周下降</th><th>五项完成</th><th>力量／有氧</th></tr></thead><tbody>${ws.map((w, i) => `<tr><td>第${w.week}周</td><td>${w.start.slice(5)}</td><td>${fmt(w.average)}</td><td>${w.weights}</td><td>${i && w.weights >= 3 && ws[i - 1].weights >= 3 ? fmt(ws[i - 1].average - w.average) : "—"}</td><td>${w.logged ? `${w.full}/${w.logged}` : "—"}</td><td>${w.strength} / ${w.cardio}</td></tr>`).join("")}</tbody></table></div></section><div class="section-title"><h2>每两周复盘</h2></div><section class="card">${state.plan.rules.map((r) => `<div class="rule"><h3>${esc(r.title)}</h3><p>${esc(r.text)}</p></div>`).join("")}</section>`;
}
function settingsPage() {
  return `<div class="welcome"><div><h1>设置</h1><p class="muted">计划日期、餐食替换和记录导出。</p></div></div><section class="card settings-section"><h2>我的90天计划</h2><form id="settings-form" class="settings-form"><label class="field">开始日期<input name="startDate" type="date" required value="${state.profile.startDate}"></label><label class="field">起始体重 kg（可留空）<input name="startWeight" type="number" inputmode="decimal" min="40" max="150" step="0.1" value="${state.profile.startWeight ?? ""}"></label><label class="field">目标体重 kg（可留空）<input name="targetWeight" type="number" inputmode="decimal" min="40" max="150" step="0.1" value="${state.profile.targetWeight ?? ""}"></label>${[
    ["stepGoal", "步数目标（可留空）", 60000],
    ["kcalGoal", "自定能量目标 kcal（可留空）", 10000],
    ["proteinGoal", "自定蛋白质目标 g（可留空）", 500],
  ]
    .map(
      ([key, label, max]) =>
        `<label class="field">${label}<input name="${key}" type="number" min="0" max="${max}" step="1" value="${state.profile[key] ?? ""}"></label>`,
    )
    .join(
      "",
    )}<div class="row"><button class="button" type="submit">保存设置</button><span class="small muted">结束日期：${plusDays(state.profile.startDate, 89)}</span></div></form><p class="save-hint">改日期后，计划与记录仍按实际日期匹配，已有记录不会删除。修改目标只更新参考曲线，餐食和训练不会自动加码。</p></section>${llmSettings()}<section class="card settings-section"><h2>餐食记录提示</h2>${state.plan.swaps.map((s) => `<details><summary>${esc(s.kind)} · ${esc(s.portion)}</summary><p>${esc(s.option)}<br>${esc(s.note)}<br>${esc(s.tip)}</p></details>`).join("")}</section><section class="card settings-section"><h2>我的记录</h2><p class="muted small">CSV可用Excel打开。JSON保存记录和分析；完整恢复请使用数据库备份。</p><div class="links"><a href="/api/export.csv">导出记录 CSV</a><a href="/api/export.json">导出记录 JSON</a><a href="/api/plan.xlsx">下载通用计划 Excel</a></div></section><section class="card settings-section"><h2>手机使用</h2><p class="muted small">浏览器菜单中选择“添加到主屏幕”，下次可直接打开。打卡需要联网；只有服务器确认保存后，页面才更新完成状态。</p><div class="links"><button id="logout" class="button ghost">退出登录</button></div></section>`;
}
function render() {
  if (!state) return;
  app.innerHTML = shell(
    tab === "today"
      ? todayPage()
      : tab === "progress"
        ? progressPage()
        : tab === "analysis"
          ? analysisPage()
          : settingsPage(),
  );
  statusIndicator();
  document.querySelectorAll("[data-tab]").forEach(
    (el) =>
      (el.onclick = () => {
        tab = el.dataset.tab;
        render();
        window.scrollTo(0, 0);
      }),
  );
  if (tab === "today") bindToday();
  if (tab === "settings") {
    bindSettings();
    bindLLM();
  }
  if (tab === "analysis") bindAnalysis();
}
function bindToday() {
  document.querySelector("#day-picker").onchange = (e) => {
    const value = e.target.value;
    if (
      value >= state.profile.startDate &&
      value <= plusDays(state.profile.startDate, 89)
    ) {
      selected = value;
      render();
    }
  };
  document.querySelector("#prev-day").onclick = () => {
    selected = plusDays(selected, -1);
    render();
  };
  document.querySelector("#next-day").onclick = () => {
    selected = plusDays(selected, 1);
    render();
  };
  document.querySelector("#today-button").onclick = () => {
    selected =
      today() < state.profile.startDate
        ? state.profile.startDate
        : today() > plusDays(state.profile.startDate, 89)
          ? plusDays(state.profile.startDate, 89)
          : today();
    render();
  };
  document.querySelectorAll("[data-complete]").forEach(
    (el) =>
      (el.onclick = async () => {
        el.disabled = true;
        await save((d) => ({
          [el.dataset.complete]:
            d[el.dataset.complete] === "完成" ? "" : "完成",
        }));
        el.disabled = false;
      }),
  );
  document
    .querySelectorAll("[data-status]")
    .forEach(
      (el) => (el.onchange = () => save({ [el.dataset.status]: el.value })),
    );
  document.querySelector("#complete-workout").onclick = async (e) => {
    const button = e.currentTarget;
    button.disabled = true;
    const rest = ["恢复", "休息"].includes(planDay().trainingType),
      done = ["完成", "计划休息"].includes(record().workout);
    const patch = { workout: done ? "" : rest ? "计划休息" : "完成" };
    await save(patch);
    button.disabled = false;
  };
  document.querySelectorAll("[data-action-done]").forEach(
    (el) =>
      (el.onchange = async () => {
        el.disabled = true;
        const id = el.dataset.actionDone,
          checked = el.checked;
        const ids = planDay().actions.map((a) => a.id);
        await save((d) => ({
          actions: { [id]: { done: checked } },
          workout: ids.every((a) => (a === id ? checked : d.actions?.[a]?.done))
            ? "完成"
            : "部分",
        }));
        el.disabled = false;
      }),
  );
  document.querySelectorAll("[data-action-weight]").forEach(
    (el) =>
      (el.onchange = () => {
        if (!el.checkValidity()) {
          el.reportValidity();
          return;
        }
        save(
          {
            actions: {
              [el.dataset.actionWeight]: {
                weight: el.value === "" ? null : Number(el.value),
              },
            },
          },
          false,
        );
      }),
  );
  document.querySelectorAll("[data-action-sets]").forEach(
    (el) =>
      (el.onchange = () => {
        if (!el.checkValidity()) {
          el.reportValidity();
          return;
        }
        save(
          {
            actions: {
              [el.dataset.actionSets]: {
                sets: el.value === "" ? null : Number(el.value),
              },
            },
          },
          false,
        );
      }),
  );
  document
    .querySelectorAll("[data-meal]")
    .forEach(
      (el) =>
        (el.onchange = () =>
          save({ mealsActual: { [el.dataset.meal]: el.value } }, false)),
    );
  document
    .querySelectorAll("[data-action-reps]")
    .forEach(
      (el) =>
        (el.onchange = () =>
          save(
            { actions: { [el.dataset.actionReps]: { reps: el.value } } },
            false,
          )),
    );
  document.querySelectorAll("[data-metric]").forEach(
    (el) =>
      (el.onchange = () => {
        if (!el.checkValidity()) {
          el.reportValidity();
          return;
        }
        const key = el.dataset.metric,
          value = [
            "weight",
            "waist",
            "steps",
            "sleep",
            "cardioMinutes",
            "cardioDistance",
          ].includes(key)
            ? el.value === ""
              ? null
              : Number(el.value)
            : el.value;
        save({ [key]: value }, false);
      }),
  );
}
function bindSettings() {
  document.querySelector("#settings-form").onsubmit = async (e) => {
    e.preventDefault();
    const form = e.currentTarget,
      data = Object.fromEntries(new FormData(form));
    if (
      data.startDate !== state.profile.startDate &&
      Object.keys(state.records).length &&
      !confirm(
        "已有记录会保留原来的实际日期。更改开始日期后，它们对应的计划天数会变化。继续更改吗？",
      )
    )
      return;
    data.startWeight =
      data.startWeight === "" ? null : Number(data.startWeight);
    data.targetWeight =
      data.targetWeight === "" ? null : Number(data.targetWeight);
    for (const key of ["stepGoal", "kcalGoal", "proteinGoal"])
      data[key] = data[key] === "" ? null : Number(data[key]);
    const b = form.querySelector("button");
    b.disabled = true;
    try {
      await saveChain;
      const result = await api("/api/profile", {
        method: "PATCH",
        body: JSON.stringify(data),
      });
      state.profile = result.profile;
      selected = state.profile.startDate;
      render();
      toast("计划设置已保存。");
    } catch (error) {
      toast(error.message);
    } finally {
      b.disabled = false;
    }
  };
  document.querySelector("#logout").onclick = async () => {
    try {
      await saveChain;
      await api("/api/logout", { method: "POST", body: "{}" });
      login();
    } catch (error) {
      toast(error.message);
    }
  };
}
function llmSettings() {
  const c = state.llm || {};
  return `<section class="card settings-section"><h2>大模型 API（可选）</h2><p class="muted small">使用自己的 OpenAI 兼容服务。密钥只在服务器保存，不回显。自定义主机需部署者在 LLM_ALLOWED_HOSTS 中允许；生产环境只支持公网 HTTPS。</p><form id="llm-form" class="settings-form"><label class="field">提供商名称<input name="provider" value="${esc(c.provider || "OpenAI 兼容")}" required maxlength="60"></label><label class="field">Base URL<input name="baseUrl" type="url" value="${esc(c.baseUrl || "https://api.openai.com/v1")}" required maxlength="300"></label><label class="field">模型名称<input name="model" value="${esc(c.model)}" required maxlength="100" placeholder="填写提供商的模型 ID"></label><label class="field">API Key${c.hasKey ? "（已配置，留空保留）" : ""}<input name="key" type="password" autocomplete="new-password" maxlength="512" ${c.hasKey ? "" : "required"} placeholder="不回显已保存密钥"></label><button class="button" type="submit">保存模型配置</button></form><div class="links"><button class="button secondary" id="llm-test">测试连接（可能收费）</button><button class="button ghost" id="llm-remove">删除 API 配置</button></div><p id="llm-message" role="status" class="muted small"></p></section>`;
}
function bindLLM() {
  document.querySelector("#llm-form").onsubmit = async (e) => {
    e.preventDefault();
    const form = e.currentTarget;
    const b = form.querySelector("button");
    b.disabled = true;
    try {
      const result = await api("/api/llm/config", {
        method: "PUT",
        body: JSON.stringify(Object.fromEntries(new FormData(form))),
      });
      form.elements.key.value = "";
      state.llm = result.llm;
      render();
      toast("模型配置已保存。");
    } catch (err) {
      toast(err.message);
    } finally {
      b.disabled = false;
    }
  };
  document.querySelector("#llm-test").onclick = async (e) => {
    if (
      !confirm(
        "连接测试将向配置的提供商发送一条测试消息，不发送健康记录，可能产生 API 费用。确认测试？",
      )
    )
      return;
    const b = e.currentTarget;
    b.disabled = true;
    try {
      const r = await api("/api/llm/test", {
        method: "POST",
        body: JSON.stringify({ confirm: true }),
      });
      document.querySelector("#llm-message").textContent = r.message;
    } catch (err) {
      document.querySelector("#llm-message").textContent = err.message;
    } finally {
      b.disabled = false;
    }
  };
  document.querySelector("#llm-remove").onclick = async () => {
    if (!confirm("删除服务器保存的 API 密钥与配置？已保存的分析仍保留。"))
      return;
    try {
      state.llm = (
        await api("/api/llm/config", { method: "DELETE", body: "{}" })
      ).llm;
      render();
      toast("配置已删除。");
    } catch (err) {
      toast(err.message);
    }
  };
}
function analysisPage() {
  return `<div class="welcome"><div><h1>模型复盘</h1><p class="muted">单日分析或最近7天趋势。只有确认摘要后才发送。</p></div></div><section class="card"><p class="notice">分析仅供参考。数据来自手动记录，缺失项为未记录。模型可能出错；API 不可用时仍可正常打卡。</p><form id="analysis-form" class="settings-form"><label class="field">结束日期<input name="end" type="date" required min="${state.profile.startDate}" max="${plusDays(state.profile.startDate, 89)}" value="${selected}"></label><label class="field">范围<select name="range"><option value="1">单日分析</option><option value="7">最近7天</option><option value="14">最近14天</option></select></label><label class="row"><input name="includeNotes" type="checkbox"><span>发送备注（默认关闭，可能包含私人信息）</span></label><button class="button" type="submit" ${state.llm?.hasKey ? "" : "disabled"}>查看待发送摘要</button></form>${state.llm?.hasKey ? "" : "<p>请先到设置页配置模型 API。</p>"}<div id="analysis-preview"></div><p id="analysis-message" role="status"></p></section><div class="section-title"><h2>已保存的分析</h2></div><section id="analysis-history">${(state.analyses || []).map((a) => `<article class="card settings-section"><h3>${esc(a.start)} ～ ${esc(a.end)}</h3><p class="muted small">${esc(a.model)} · ${esc(new Date(a.created * 1000).toLocaleString("zh-CN"))}</p><pre class="analysis-text">${esc(a.result)}</pre><button class="button ghost small" data-delete-analysis="${esc(a.id)}">删除此分析</button></article>`).join("") || '<p class="muted">尚无分析。预览不会调用模型。</p>'}</section>`;
}
function bindAnalysis() {
  document.querySelector("#analysis-form").onsubmit = async (e) => {
    e.preventDefault();
    const form = e.currentTarget,
      b = form.querySelector("button");
    b.disabled = true;
    document.querySelector("#analysis-preview").replaceChildren();
    const data = Object.fromEntries(new FormData(form));
    const start = plusDays(data.end, -Number(data.range) + 1);
    try {
      await saveChain;
      const r = await api("/api/llm/preview", {
        method: "POST",
        body: JSON.stringify({
          start:
            start < state.profile.startDate ? state.profile.startDate : start,
          end: data.end,
          includeNotes: form.elements.includeNotes.checked,
        }),
      });
      const box = document.querySelector("#analysis-preview");
      box.innerHTML = `<h3>发送前确认</h3><p>${esc(r.warning)}</p><p>提供商：${esc(r.provider.provider)} · ${esc(r.provider.baseUrl)} · ${esc(r.provider.model)}</p><details open><summary>查看全部待发送的健康数据</summary><pre class="analysis-text">${esc(JSON.stringify(r.payload, null, 2))}</pre></details><label class="row"><input id="analysis-consent" type="checkbox"><span>我已查看摘要，同意向上述服务商发送，并了解可能产生 API 费用</span></label><button id="analysis-send" class="button" disabled>确认发送并分析</button>`;
      const send = document.querySelector("#analysis-send");
      document.querySelector("#analysis-consent").onchange = (e) =>
        (send.disabled = !e.target.checked);
      send.onclick = async () => {
        send.disabled = true;
        document.querySelector("#analysis-message").textContent =
          "正在分析，最长约25秒。请勿重复提交…";
        try {
          const result = await api("/api/llm/analyze", {
            method: "POST",
            body: JSON.stringify({ previewId: r.previewId, confirm: true }),
          });
          state.analyses.unshift(result.analysis);
          render();
          toast("分析已保存。");
        } catch (err) {
          document.querySelector("#analysis-message").textContent = err.message;
          box.replaceChildren();
        }
      };
    } catch (err) {
      document.querySelector("#analysis-message").textContent = err.message;
    } finally {
      b.disabled = false;
    }
  };
  document.querySelectorAll("[data-delete-analysis]").forEach(
    (el) =>
      (el.onclick = async () => {
        if (!confirm("删除这条分析？")) return;
        try {
          await api("/api/llm/delete", {
            method: "POST",
            body: JSON.stringify({ id: el.dataset.deleteAnalysis }),
          });
          state.analyses = state.analyses.filter(
            (a) => a.id !== el.dataset.deleteAnalysis,
          );
          render();
        } catch (err) {
          toast(err.message);
        }
      }),
  );
}
window.addEventListener("online", () => {
  statusIndicator();
  toast("已恢复连接，可以继续保存。");
});
window.addEventListener("offline", () => {
  statusIndicator();
  toast("当前离线，打卡暂不能保存。");
});
window.addEventListener("beforeunload", (e) => {
  if (saving) {
    e.preventDefault();
    e.returnValue = "";
  }
});
load();
