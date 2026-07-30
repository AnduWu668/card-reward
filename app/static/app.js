const state = { users: [], cards: [], userId: localStorage.getItem("demoUserId"), selected: null };
const $ = (selector) => document.querySelector(selector);
const symbols = { QINGLONG: "青", ZHUQUE: "朱", BAIHU: "白", XUANWU: "玄", QILIN: "麒" };

function key() {
  return crypto.randomUUID();
}

function headers(withIdempotency = false) {
  const result = { "Content-Type": "application/json", "X-Demo-User-Id": state.userId };
  if (withIdempotency) result["Idempotency-Key"] = key();
  return result;
}

async function api(path, options = {}) {
  const response = await fetch(path, options);
  const contentType = response.headers.get("content-type") || "";
  const body = contentType.includes("application/json")
    ? await response.json()
    : { error: { message: "服务暂时不可用，请稍后重试" } };
  if (!response.ok) {
    if (body.error?.code === "DEMO_USER_NOT_FOUND") {
      localStorage.removeItem("demoUserId");
      location.reload();
    }
    throw new Error(body.error?.message || "请求失败");
  }
  return body;
}

function toast(message) {
  const node = $("#toast");
  node.textContent = message;
  node.classList.add("show");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => node.classList.remove("show"), 2200);
}

function selectCard(card) {
  state.selected = card;
  $("#card").classList.remove("flipped");
  $("#cardName").textContent = card.name;
  $("#cardRarity").textContent = card.rarity === "RARE" ? "稀有 · 祥瑞" : "五灵 · 启示";
  $("#cardSigil").textContent = symbols[card.code];
  $("#cardQuestion").textContent = card.question;
  $("#cardInterpretation").textContent = card.interpretation;
  document.querySelectorAll(".mini-card").forEach((node) => {
    node.classList.toggle("selected", node.dataset.id === card.id);
  });
}

function renderCards() {
  $("#progress").innerHTML = `${state.cards.filter((card) => card.quantity > 0).length}<em>/5</em>`;
  $("#cardList").innerHTML = state.cards.map((card) => `
    <button class="mini-card ${card.quantity ? "owned" : ""}" data-id="${card.id}">
      <span class="mini-visual">${symbols[card.code]}<b class="mini-count">${card.quantity}</b></span>
      <span class="mini-name">${card.name}</span>
    </button>
  `).join("");
  document.querySelectorAll(".mini-card").forEach((node) => {
    node.addEventListener("click", () => selectCard(state.cards.find((card) => card.id === node.dataset.id)));
  });
  selectCard(state.selected ? state.cards.find((card) => card.id === state.selected.id) || state.cards[0] : state.cards[0]);
}

async function loadCards() {
  state.cards = await api("/api/v1/cards", { headers: headers() });
  renderCards();
  $("#drawButton").disabled = false;
  $("#drawButton").textContent = "抽卡（点击开启今日灵运）";
}

async function loadUsers() {
  state.users = await api("/api/v1/demo/users");
  if (!state.userId || !state.users.some((user) => user.id === state.userId)) state.userId = state.users[0].id;
  $("#userSelect").innerHTML = state.users.map((user) =>
    `<option value="${user.id}" ${user.id === state.userId ? "selected" : ""}>${user.nickname}</option>`
  ).join("");
  localStorage.setItem("demoUserId", state.userId);
  await loadCards();
}

async function draw() {
  const button = $("#drawButton");
  const cardNode = $("#card");
  button.disabled = true;
  cardNode.classList.remove("flipped");
  cardNode.classList.add("drawing");
  try {
    const result = await api("/api/v1/draws", { method: "POST", headers: headers(true) });
    await new Promise((resolve) => setTimeout(resolve, 850));
    state.cards = state.cards.map((card) => card.id === result.card.id ? result.card : card);
    state.selected = result.card;
    renderCards();
    $("#resultSigil").textContent = symbols[result.card.code];
    $("#resultTitle").textContent = `抽到${result.card.name}`;
    $("#resultText").textContent = result.card.question;
    $("#giftButton").hidden = currentUser().role === "USER";
    $("#shareBox").hidden = true;
    $("#resultDialog").showModal();
    button.textContent = `抽卡（今日还剩 ${result.draws_remaining_today} 次）`;
    button.disabled = result.draws_remaining_today === 0;
  } catch (error) {
    toast(error.message);
    button.disabled = false;
  } finally {
    cardNode.classList.remove("drawing");
  }
}

function currentUser() {
  return state.users.find((user) => user.id === state.userId);
}

async function createGift() {
  try {
    const gift = await api("/api/v1/gift-links", {
      method: "POST",
      headers: headers(true),
      body: JSON.stringify({ card_type_id: state.selected.id })
    });
    $("#shareUrl").value = gift.share_url;
    $("#shareBox").hidden = false;
  } catch (error) {
    toast(error.message);
  }
}

async function loadGift(token) {
  try {
    const gift = await api(`/api/v1/gift-links/${encodeURIComponent(token)}`);
    $("#claimSigil").textContent = symbols[gift.card.code];
    $("#claimTitle").textContent = `${gift.sender_nickname} 赠你${gift.card.name}`;
    $("#claimText").textContent = gift.card.question;
    $("#claimButton").dataset.token = token;
    $("#claimDialog").showModal();
  } catch (error) {
    toast(error.message);
  }
}

async function claim() {
  try {
    const result = await api(`/api/v1/gift-links/${encodeURIComponent($("#claimButton").dataset.token)}/claims`, {
      method: "POST",
      headers: headers(true)
    });
    toast(`成功领取${result.card.name}`);
    $("#claimDialog").close();
    await loadCards();
  } catch (error) {
    toast(error.message);
  }
}

$("#userSelect").addEventListener("change", async (event) => {
  state.userId = event.target.value;
  state.selected = null;
  localStorage.setItem("demoUserId", state.userId);
  await loadCards();
});
$("#drawButton").addEventListener("click", draw);
$("#card").addEventListener("click", () => $("#card").classList.toggle("flipped"));
$("#card").addEventListener("keydown", (event) => {
  if (event.key === "Enter" || event.key === " ") $("#card").click();
});
$("#giftButton").addEventListener("click", createGift);
$("#copyButton").addEventListener("click", async () => {
  await navigator.clipboard.writeText($("#shareUrl").value);
  toast("赠卡链接已复制");
});
$("#claimButton").addEventListener("click", claim);
document.querySelectorAll("[data-close]").forEach((node) =>
  node.addEventListener("click", () => node.closest("dialog").close())
);

loadUsers().then(() => {
  const match = location.pathname.match(/^\/g\/(.+)$/);
  if (match) loadGift(decodeURIComponent(match[1]));
}).catch((error) => toast(error.message));
