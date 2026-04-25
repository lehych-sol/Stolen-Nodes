import { initializeApp } from "https://www.gstatic.com/firebasejs/10.12.5/firebase-app.js";
import {
  getAuth,
  GoogleAuthProvider,
  onAuthStateChanged,
  signInWithPopup,
  signOut,
} from "https://www.gstatic.com/firebasejs/10.12.5/firebase-auth.js";
import {
  collection,
  getFirestore,
  limit,
  onSnapshot,
  orderBy,
  query,
} from "https://www.gstatic.com/firebasejs/10.12.5/firebase-firestore.js";

const firebaseConfig = window.DARKEN_FIREBASE_CONFIG || {};
const adminEmails = new Set(
  (window.DARKEN_ADMIN_EMAILS || []).map((item) => `${item}`.trim().toLowerCase()).filter(Boolean),
);

const loginButton = document.querySelector("#loginButton");
const logoutButton = document.querySelector("#logoutButton");
const searchInput = document.querySelector("#searchInput");
const statusText = document.querySelector("#statusText");
const connectionBadge = document.querySelector("#connectionBadge");
const activeUser = document.querySelector("#activeUser");
const totalCount = document.querySelector("#totalCount");
const successCount = document.querySelector("#successCount");
const failedCount = document.querySelector("#failedCount");
const clientCount = document.querySelector("#clientCount");
const lastSync = document.querySelector("#lastSync");
const feedCountLabel = document.querySelector("#feedCountLabel");
const selectedMode = document.querySelector("#selectedMode");
const selectedSeed = document.querySelector("#selectedSeed");
const selectedAspect = document.querySelector("#selectedAspect");
const selectedPrompt = document.querySelector("#selectedPrompt");
const selectedSummary = document.querySelector("#selectedSummary");
const galleryGrid = document.querySelector("#galleryGrid");
const pulseList = document.querySelector("#pulseList");
const detailModal = document.querySelector("#detailModal");
const closeModalButton = document.querySelector("#closeModalButton");

const modalTitle = document.querySelector("#modalTitle");
const modalImageStage = document.querySelector("#modalImageStage");
const modalSeed = document.querySelector("#modalSeed");
const modalClient = document.querySelector("#modalClient");
const modalAspect = document.querySelector("#modalAspect");
const modalStatus = document.querySelector("#modalStatus");
const modalMode = document.querySelector("#modalMode");
const modalDate = document.querySelector("#modalDate");
const modalPrompt = document.querySelector("#modalPrompt");
const modalNegative = document.querySelector("#modalNegative");
const modalSummary = document.querySelector("#modalSummary");
const modalTaskId = document.querySelector("#modalTaskId");
const modalMetadata = document.querySelector("#modalMetadata");

let allDocs = [];
let filteredDocs = [];
let selectedId = null;
let modalItem = null;
let unsubscribe = null;

function configReady() {
  return firebaseConfig.apiKey && firebaseConfig.projectId && firebaseConfig.appId;
}

function escapeHtml(value) {
  return `${value ?? ""}`
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll("\"", "&quot;")
    .replaceAll("'", "&#39;");
}

function formatDate(value) {
  if (!value) return "Unknown";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "Unknown" : date.toLocaleString();
}

function formatTimeShort(value) {
  if (!value) return "--";
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? "--"
    : date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function clampText(value, maxLength = 140) {
  const text = `${value ?? ""}`.trim();
  if (!text) return "";
  return text.length > maxLength ? `${text.slice(0, maxLength - 3)}...` : text;
}

async function copyText(value) {
  const text = `${value ?? ""}`.trim();
  if (!text) return;
  try {
    await navigator.clipboard.writeText(text);
    setStatus("Copied to clipboard.", "online");
  } catch (error) {
    console.error(error);
    setStatus("Copy failed in this browser.", "warn");
  }
}

function setStatus(message, tone = "offline") {
  statusText.textContent = message;
  connectionBadge.textContent = tone === "online" ? "live feed" : tone === "warn" ? "attention" : "not connected";
  connectionBadge.className = `connection-badge ${tone}`;
}

function buildCounts(items) {
  const completed = items.filter((item) => `${item.status || ""}`.toUpperCase() === "COMPLETED").length;
  const failed = items.filter((item) => `${item.status || ""}`.toUpperCase() !== "COMPLETED").length;
  const clientSet = new Set(items.map((item) => `${item.clientLabel || ""}`.trim()).filter(Boolean));

  totalCount.textContent = `${items.length}`;
  successCount.textContent = `${completed}`;
  failedCount.textContent = `${failed}`;
  clientCount.textContent = `${clientSet.size}`;
  lastSync.textContent = items[0]?.createdAtMs ? formatTimeShort(items[0].createdAtMs) : "--";
  feedCountLabel.textContent = `${items.length} ${items.length === 1 ? "item" : "items"}`;
}

function getSelectedItem(items) {
  if (!items.length) {
    selectedId = null;
    return null;
  }

  const selected = items.find((item) => item.id === selectedId);
  if (selected) return selected;

  selectedId = items[0].id;
  return items[0];
}

function renderSidebarSelection(item) {
  if (!item) {
    selectedMode.textContent = "--";
    selectedSeed.textContent = "--";
    selectedAspect.textContent = "--";
    selectedPrompt.textContent = "No generation selected yet.";
    selectedSummary.textContent = "Feed summary will appear here.";
    return;
  }

  selectedMode.textContent = item.mode || "--";
  selectedSeed.textContent = `${item.seed ?? "--"}`;
  selectedAspect.textContent = item.aspectRatioLabel || item.aspectRatio || "--";
  selectedPrompt.textContent = item.prompt || "No prompt recorded.";
  selectedSummary.textContent = item.failureMessage || item.summary || "No summary recorded.";
}

function renderPulse(items) {
  if (!items.length) {
    pulseList.innerHTML = '<div class="gallery-empty">No activity yet.</div>';
    return;
  }

  pulseList.innerHTML = items
    .slice(0, 6)
    .map((item) => {
      const failed = `${item.status || ""}`.toUpperCase() !== "COMPLETED";
      return `
        <button class="pulse-item ${item.id === selectedId ? "active" : ""}" type="button" data-open-id="${escapeHtml(item.id)}">
          <div class="pulse-top">
            <small>${escapeHtml(item.clientLabel || "Unknown client")}</small>
            <span class="status-chip ${failed ? "failed" : ""}">${escapeHtml(item.status || "UNKNOWN")}</span>
          </div>
          <p>${escapeHtml(clampText(item.summary || item.prompt || "No summary", 88))}</p>
        </button>
      `;
    })
    .join("");
}

function renderGallery(items) {
  if (!items.length) {
    galleryGrid.innerHTML = '<div class="gallery-empty">No generation events yet.</div>';
    return;
  }

  galleryGrid.innerHTML = items
    .map((item) => {
      const failed = `${item.status || ""}`.toUpperCase() !== "COMPLETED";
      const preview = item.previewDataUrl
        ? `<img src="${item.previewDataUrl}" alt="Generation preview" />`
        : '<div class="gallery-card-placeholder">No preview</div>';

      return `
        <button class="gallery-card ${item.id === selectedId ? "active" : ""}" type="button" data-open-id="${escapeHtml(item.id)}">
          <div class="gallery-card-media">${preview}</div>
          <div class="gallery-card-overlay">
            <div class="gallery-meta-top">
              <small>${escapeHtml(item.clientLabel || "Unknown client")}</small>
              <span class="status-chip ${failed ? "failed" : ""}">${escapeHtml(item.status || "UNKNOWN")}</span>
            </div>
            <h3>${escapeHtml(clampText(item.prompt || item.summary || "No prompt", 76))}</h3>
            <p>${escapeHtml(clampText(item.negativePrompt || item.summary || "No extra detail", 96))}</p>
            <div class="gallery-card-bottom">
              <small>seed ${escapeHtml(item.seed ?? "--")}</small>
              <small>${escapeHtml(formatTimeShort(item.createdAtMs))}</small>
            </div>
          </div>
        </button>
      `;
    })
    .join("");
}

function openModal(item) {
  if (!item) return;
  modalItem = item;
  modalTitle.textContent = item.clientLabel || "Tracked generation";
  modalImageStage.innerHTML = item.previewDataUrl
    ? `<img src="${item.previewDataUrl}" alt="Tracked generation preview" />`
    : '<div class="modal-image-placeholder">No preview image was captured for this event.</div>';
  modalSeed.textContent = `${item.seed ?? "--"}`;
  modalClient.textContent = item.clientLabel || "--";
  modalAspect.textContent = item.aspectRatioLabel || item.aspectRatio || "--";
  modalStatus.textContent = item.status || "--";
  modalMode.textContent = item.mode || "--";
  modalDate.textContent = formatDate(item.createdAtMs);
  modalPrompt.textContent = item.prompt || "No prompt recorded.";
  modalNegative.textContent = item.negativePrompt || "No negative prompt recorded.";
  modalSummary.textContent = item.failureMessage || item.summary || "No summary recorded.";
  modalTaskId.textContent = item.taskId || "--";
  modalMetadata.textContent = item.metadataPath || "--";
  detailModal.hidden = false;
  document.body.classList.add("modal-open");
}

function closeModal() {
  detailModal.hidden = true;
  modalItem = null;
  document.body.classList.remove("modal-open");
}

function renderAll(items) {
  buildCounts(items);
  const selected = getSelectedItem(items);
  renderSidebarSelection(selected);
  renderPulse(items);
  renderGallery(items);
}

function applyFilter() {
  const needle = `${searchInput.value || ""}`.trim().toLowerCase();
  filteredDocs = !needle
    ? [...allDocs]
    : allDocs.filter((item) => {
        const haystack = [
          item.prompt,
          item.negativePrompt,
          item.summary,
          item.clientLabel,
          item.seed,
          item.mode,
          item.status,
          item.taskId,
          item.aspectRatioLabel,
        ]
          .join(" ")
          .toLowerCase();
        return haystack.includes(needle);
      });
  renderAll(filteredDocs);
}

function mountRealtimeFeed(db) {
  const q = query(collection(db, "generations"), orderBy("createdAtMs", "desc"), limit(120));
  unsubscribe = onSnapshot(
    q,
    (snapshot) => {
      allDocs = snapshot.docs.map((doc) => ({ id: doc.id, ...doc.data() }));
      setStatus(`Live feed connected. ${allDocs.length} records loaded.`, "online");
      applyFilter();
    },
    (error) => {
      console.error(error);
      setStatus(`Feed error: ${error.message}`, "warn");
    },
  );
}

function teardownFeed() {
  if (typeof unsubscribe === "function") {
    unsubscribe();
    unsubscribe = null;
  }
  allDocs = [];
  filteredDocs = [];
  selectedId = null;
  renderAll([]);
}

document.addEventListener("click", (event) => {
  const opener = event.target.closest("[data-open-id]");
  if (opener) {
    const id = opener.getAttribute("data-open-id");
    selectedId = id;
    renderAll(filteredDocs);
    const item = filteredDocs.find((entry) => entry.id === id) || allDocs.find((entry) => entry.id === id);
    openModal(item);
    return;
  }

  const closer = event.target.closest("[data-close-modal]");
  if (closer) {
    closeModal();
    return;
  }

  const copyButton = event.target.closest("[data-copy-field]");
  if (copyButton && modalItem) {
    const field = copyButton.getAttribute("data-copy-field");
    if (field === "seed") {
      copyText(modalItem.seed);
    } else if (field === "prompt") {
      copyText(modalItem.prompt);
    }
  }
});

closeModalButton.addEventListener("click", closeModal);

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && !detailModal.hidden) {
    closeModal();
  }
});

if (!configReady()) {
  setStatus("Edit public/config.js first, then reload the page.", "warn");
} else {
  const firebaseApp = initializeApp(firebaseConfig);
  const auth = getAuth(firebaseApp);
  const db = getFirestore(firebaseApp);
  const provider = new GoogleAuthProvider();

  loginButton.addEventListener("click", async () => {
    try {
      await signInWithPopup(auth, provider);
    } catch (error) {
      console.error(error);
      setStatus(`Login failed: ${error.message}`, "warn");
    }
  });

  logoutButton.addEventListener("click", async () => {
    await signOut(auth);
  });

  searchInput.addEventListener("input", applyFilter);

  onAuthStateChanged(auth, (user) => {
    const email = `${user?.email || ""}`.trim().toLowerCase();
    activeUser.textContent = email || "signed out";

    if (!user) {
      teardownFeed();
      loginButton.hidden = false;
      logoutButton.hidden = true;
      setStatus("Sign in with an approved Google account.", "offline");
      return;
    }

    if (!adminEmails.has(email)) {
      teardownFeed();
      loginButton.hidden = true;
      logoutButton.hidden = false;
      setStatus(`Signed in as ${email}, but this email is not approved.`, "warn");
      return;
    }

    loginButton.hidden = true;
    logoutButton.hidden = false;
    setStatus(`Signed in as ${email}. Connecting live feed...`, "online");
    mountRealtimeFeed(db);
  });
}
