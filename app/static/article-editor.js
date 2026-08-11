(() => {
  const editor = document.querySelector("#article-content");
  const titleInput = document.querySelector("#article-title");
  const statusInput = document.querySelector("#article-status");
  const form = document.querySelector("#article-editor-form");
  const workspace = document.querySelector("#editor-workspace");
  const preview = document.querySelector("#markdown-preview");
  const previewState = document.querySelector("#preview-state");
  const editorState = document.querySelector("#editor-state");
  const saveState = document.querySelector("#save-state");
  const stats = document.querySelector("#editor-stats");
  if (!editor || !preview) return;

  let imageData = {};
  try {
    imageData = JSON.parse(document.querySelector("#article-image-data")?.textContent || "{}");
  } catch (_) {
    imageData = {};
  }

  let initialState = {
    title: titleInput?.value || "",
    content: editor.value,
    status: statusInput?.value || "draft",
  };
  const draftKey = `content-idea-workflow:article-draft:${window.location.pathname}`;
  const autosaveUrl = form?.dataset.autosaveUrl || "";
  let previewTimer;
  let draftTimer;
  let autosaveTimer;
  let requestNumber = 0;
  let hasUnsavedChanges = false;
  let autosaveInFlight = false;
  let lastSelection = { start: editor.selectionStart, end: editor.selectionEnd };
  let history = [editor.value];
  let historyIndex = 0;
  let restoringHistory = false;

  const formatTime = (timestamp) => {
    try {
      return new Date(timestamp).toLocaleString("zh-CN", { hour: "2-digit", minute: "2-digit" });
    } catch (_) {
      return "刚刚";
    }
  };

  const currentState = () => ({
    title: titleInput?.value || "",
    content: editor.value,
    status: statusInput?.value || "draft",
  });

  const sameState = (left, right) => left.title === right.title && left.content === right.content && left.status === right.status;
  const stateChanged = () => !sameState(currentState(), initialState);

  const rememberSelection = () => {
    lastSelection = {
      start: Math.max(0, Math.min(editor.selectionStart, editor.value.length)),
      end: Math.max(0, Math.min(editor.selectionEnd, editor.value.length)),
    };
  };

  const selectionRange = () => {
    const active = document.activeElement === editor;
    const range = active ? { start: editor.selectionStart, end: editor.selectionEnd } : lastSelection;
    const start = Math.max(0, Math.min(Number(range.start) || 0, editor.value.length));
    const end = Math.max(start, Math.min(Number(range.end) || start, editor.value.length));
    return { start, end };
  };

  const updateStats = () => {
    const value = editor.value || "";
    const characters = [...value.replace(/\s/g, "")].length;
    const paragraphs = value.split(/\n\s*\n/).map((item) => item.trim()).filter(Boolean).length;
    const headings = [...value.matchAll(/^#{1,6}\s+.+$/gm)].length;
    const images = [...value.matchAll(/!\[[^\]]*\]\([^)]*\)/g)].length;
    if (stats) stats.textContent = `${characters.toLocaleString("zh-CN")} 字 · ${paragraphs} 段 · ${headings} 个标题 · ${images} 张图`;
  };

  const updateDirtyState = () => {
    hasUnsavedChanges = stateChanged();
    if (editorState) {
      editorState.textContent = hasUnsavedChanges
        ? "有未保存的修改 · 正在保护你的草稿"
        : "已保存 · 继续编辑即可";
    }
    if (saveState && !hasUnsavedChanges && !autosaveInFlight) saveState.textContent = "当前内容已保存";
  };

  const saveLocalDraft = () => {
    clearTimeout(draftTimer);
    draftTimer = setTimeout(() => {
      updateDirtyState();
      if (!hasUnsavedChanges) {
        try { localStorage.removeItem(draftKey); } catch (_) { /* private browsing can reject storage */ }
        return;
      }
      try {
        localStorage.setItem(draftKey, JSON.stringify({ ...currentState(), savedAt: Date.now() }));
        if (saveState) saveState.textContent = `本机临时副本已保存 · ${formatTime(Date.now())}；服务器每 30 秒自动保存`;
      } catch (_) {
        if (saveState) saveState.textContent = "浏览器未允许本机草稿保存，请及时点击“保存文章”";
      }
    }, 450);
  };

  const scheduleAutosave = () => {
    clearTimeout(autosaveTimer);
    autosaveTimer = setTimeout(() => requestAutosave("编辑后"), 5000);
  };

  async function requestAutosave(reason = "定时") {
    updateDirtyState();
    if (!autosaveUrl || !hasUnsavedChanges || autosaveInFlight) return;
    const payload = currentState();
    autosaveInFlight = true;
    if (saveState) saveState.textContent = `正在自动保存（${reason}）…`;
    try {
      const response = await fetch(autosaveUrl, {
        method: "POST",
        headers: { "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8", Accept: "application/json" },
        body: new URLSearchParams(payload),
      });
      if (!response.ok) throw new Error(`autosave ${response.status}`);
      const saved = await response.json();
      if (sameState(currentState(), payload)) {
        initialState = payload;
        hasUnsavedChanges = false;
        try { localStorage.removeItem(draftKey); } catch (_) { /* ignore */ }
        if (saveState) saveState.textContent = `已自动保存 · ${formatTime(saved.updated_at || Date.now())}`;
        if (editorState) editorState.textContent = "已自动保存 · 继续编辑即可";
      } else if (saveState) {
        saveState.textContent = "已保存上一版，当前新修改仍在本机保护中";
      }
    } catch (_) {
      if (saveState) saveState.textContent = "自动保存暂时失败 · 本机副本仍在保护，请检查服务后手动保存";
    } finally {
      autosaveInFlight = false;
      updateDirtyState();
    }
  }

  const refreshHeadingOptions = () => {
    const headings = [...editor.value.matchAll(/^#{1,6}\s+(.+?)\s*$/gm)].map((match) => match[1].trim()).filter(Boolean);
    document.querySelectorAll(".image-position-select").forEach((select) => {
      const current = select.value;
      [...select.options].filter((option) => option.dataset.dynamicHeading === "true").forEach((option) => option.remove());
      headings.forEach((heading) => {
        const option = document.createElement("option");
        option.value = `heading:${encodeURIComponent(heading)}`;
        option.textContent = `标题后：${heading.slice(0, 24)}`;
        option.dataset.dynamicHeading = "true";
        select.appendChild(option);
      });
      if ([...select.options].some((option) => option.value === current)) select.value = current;
    });
  };

  const refreshPreview = () => {
    clearTimeout(previewTimer);
    rememberSelection();
    updateDirtyState();
    updateStats();
    refreshHeadingOptions();
    saveLocalDraft();
    scheduleAutosave();
    if (previewState) previewState.textContent = "更新中…";
    previewTimer = setTimeout(async () => {
      const currentRequest = ++requestNumber;
      try {
        const body = new URLSearchParams({ content: editor.value });
        const response = await fetch(editor.dataset.previewUrl, {
          method: "POST",
          headers: { "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8" },
          body,
        });
        if (!response.ok) throw new Error("preview failed");
        const html = await response.text();
        if (currentRequest === requestNumber) {
          preview.innerHTML = html;
          if (previewState) previewState.textContent = "预览已更新";
        }
      } catch (_) {
        if (currentRequest === requestNumber && previewState) previewState.textContent = "预览暂时不可用";
      }
    }, 350);
  };

  const pushHistory = () => {
    if (restoringHistory || history[historyIndex] === editor.value) return;
    history = history.slice(0, historyIndex + 1);
    history.push(editor.value);
    if (history.length > 60) history.shift();
    historyIndex = history.length - 1;
  };

  const applyEditorValue = (value) => {
    editor.value = value;
    editor.focus();
    editor.setSelectionRange(editor.value.length, editor.value.length);
    rememberSelection();
    editor.dispatchEvent(new Event("input", { bubbles: true }));
  };

  const replaceSelection = (value, selectionStart = value.length, selectionEnd = selectionStart) => {
    const { start, end } = selectionRange();
    editor.focus();
    editor.setSelectionRange(start, end);
    editor.setRangeText(value, start, end, "select");
    editor.setSelectionRange(start + selectionStart, start + selectionEnd);
    rememberSelection();
    editor.dispatchEvent(new Event("input", { bubbles: true }));
  };

  const wrapSelection = (before, after, placeholder) => {
    const { start, end } = selectionRange();
    const selected = editor.value.slice(start, end);
    const body = selected || placeholder;
    replaceSelection(`${before}${body}${after}`, before.length, before.length + body.length);
  };

  const toggleLinePrefix = (prefix, pattern) => {
    const { start, end } = selectionRange();
    const lineStart = editor.value.lastIndexOf("\n", Math.max(0, start - 1)) + 1;
    const lineEndIndex = editor.value.indexOf("\n", end);
    const lineEnd = lineEndIndex === -1 ? editor.value.length : lineEndIndex;
    const source = editor.value.slice(lineStart, lineEnd);
    const lines = source.split("\n");
    const shouldRemove = lines.filter((line) => line.trim()).every((line) => pattern.test(line));
    const replacement = lines.map((line) => {
      if (!line.trim()) return line;
      return shouldRemove ? line.replace(pattern, "") : `${prefix}${line}`;
    }).join("\n");
    editor.focus();
    editor.setRangeText(replacement, lineStart, lineEnd, "select");
    editor.setSelectionRange(lineStart, lineStart + replacement.length);
    rememberSelection();
    editor.dispatchEvent(new Event("input", { bubbles: true }));
  };

  const formatHeading = (level) => {
    const prefix = `${"#".repeat(level)} `;
    const { start, end } = selectionRange();
    const lineStart = editor.value.lastIndexOf("\n", Math.max(0, start - 1)) + 1;
    const lineEndIndex = editor.value.indexOf("\n", end);
    const lineEnd = lineEndIndex === -1 ? editor.value.length : lineEndIndex;
    const source = editor.value.slice(lineStart, lineEnd);
    const replacement = source.split("\n").map((line) => `${prefix}${line.replace(/^#{1,6}\s*/, "")}`).join("\n");
    editor.focus();
    editor.setRangeText(replacement, lineStart, lineEnd, "select");
    editor.setSelectionRange(lineStart, lineStart + replacement.length);
    rememberSelection();
    editor.dispatchEvent(new Event("input", { bubbles: true }));
  };

  const undo = () => {
    if (historyIndex <= 0) return;
    historyIndex -= 1;
    restoringHistory = true;
    editor.value = history[historyIndex];
    editor.focus();
    editor.setSelectionRange(editor.value.length, editor.value.length);
    rememberSelection();
    editor.dispatchEvent(new Event("input", { bubbles: true }));
    restoringHistory = false;
  };

  const redo = () => {
    if (historyIndex >= history.length - 1) return;
    historyIndex += 1;
    restoringHistory = true;
    editor.value = history[historyIndex];
    editor.focus();
    editor.setSelectionRange(editor.value.length, editor.value.length);
    rememberSelection();
    editor.dispatchEvent(new Event("input", { bubbles: true }));
    restoringHistory = false;
  };

  const runCommand = (command) => {
    if (command === "h1" || command === "h2" || command === "h3") return formatHeading(Number(command.slice(1)));
    if (command === "bold") return wrapSelection("**", "**", "重点内容");
    if (command === "italic") return wrapSelection("*", "*", "强调内容");
    if (command === "strike") return wrapSelection("~~", "~~", "删除线内容");
    if (command === "quote") return toggleLinePrefix("> ", /^>\s?/);
    if (command === "ul") return toggleLinePrefix("- ", /^[-*+]\s+/);
    if (command === "ol") return toggleLinePrefix("1. ", /^\d+\.\s+/);
    if (command === "link") {
      const { start, end } = selectionRange();
      const selected = editor.value.slice(start, end) || "链接文字";
      const value = `[${selected}](https://example.com)`;
      return replaceSelection(value, selected.length + 3, selected.length + 3 + "https://example.com".length);
    }
    if (command === "code") return wrapSelection("`", "`", "代码");
    if (command === "table") return replaceSelection("| 项目 | 说明 |\n| --- | --- |\n| 内容 | 请填写 |", 2, 2);
    if (command === "callout") return replaceSelection("> **提示**：请填写需要提醒读者的内容。", 10, 10);
    if (command === "hr") return replaceSelection("\n\n---\n\n", 4, 4);
    if (command === "undo") return undo();
    if (command === "redo") return redo();
  };

  const setViewMode = (mode) => {
    if (!workspace) return;
    workspace.classList.remove("view-split", "view-edit", "view-preview");
    workspace.classList.add(`view-${mode}`);
    document.querySelectorAll("[data-view-mode]").forEach((button) => {
      const active = button.dataset.viewMode === mode;
      button.classList.toggle("is-active", active);
      button.setAttribute("aria-pressed", active ? "true" : "false");
    });
  };

  const insertAtCursor = (markdown) => {
    const { start, end } = selectionRange();
    const before = editor.value.slice(0, start);
    const after = editor.value.slice(end);
    const beforeGap = before && !before.endsWith("\n\n") ? "\n\n" : "";
    const afterGap = after && !after.startsWith("\n\n") ? "\n\n" : "";
    const insertion = `${beforeGap}${markdown}${afterGap}`;
    editor.focus();
    editor.setSelectionRange(start, end);
    editor.setRangeText(insertion, start, end, "end");
    rememberSelection();
    editor.dispatchEvent(new Event("input", { bubbles: true }));
  };

  const insertAtPosition = (markdown, position) => {
    if (position === "cursor") return insertAtCursor(markdown);
    if (position?.startsWith("heading:")) {
      const heading = decodeURIComponent(position.slice("heading:".length));
      const expression = new RegExp(`^#{1,6}\\s+${heading.replace(/[.*+?^${}()|[\\]\\\\]/g, "\\\\$&")}\\s*$`, "m");
      const match = expression.exec(editor.value);
      if (match) {
        const point = match.index + match[0].length;
        editor.focus();
        editor.setSelectionRange(point, point);
        rememberSelection();
        return insertAtCursor(markdown);
      }
    }
    const blocks = editor.value.trim().split(/\n{2,}/).filter(Boolean);
    let index = blocks.length;
    if (position === "start") index = blocks[0]?.startsWith("#") ? 1 : 0;
    if (position === "after_first") index = Math.min(blocks.length, Math.max(1, blocks.findIndex((block) => !block.startsWith("#")) + 1));
    if (position === "middle") index = Math.max(1, Math.floor(blocks.length / 2));
    blocks.splice(Math.min(index, blocks.length), 0, markdown);
    applyEditorValue(blocks.join("\n\n").trim());
  };

  const markImageInserted = (imageId) => {
    const card = document.querySelector(`[data-image-card][data-image-id="${CSS.escape(String(imageId))}"]`);
    if (!card) return;
    card.dataset.imageInserted = "true";
    card.querySelectorAll(".insert-at-cursor, .insert-selected-image").forEach((button) => {
      button.disabled = true;
      button.textContent = "已在正文";
    });
    if (!card.querySelector(".inserted-badge")) {
      const badge = document.createElement("span");
      badge.className = "inserted-badge";
      badge.textContent = "已在正文";
      card.querySelector(".material-image-wrap")?.appendChild(badge);
    }
  };

  const copyText = async (text) => {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch (_) {
      const helper = document.createElement("textarea");
      helper.value = text;
      document.body.appendChild(helper);
      helper.select();
      const copied = document.execCommand("copy");
      helper.remove();
      return copied;
    }
  };

  ["focus", "select", "keyup", "mouseup", "input"].forEach((eventName) => editor.addEventListener(eventName, rememberSelection));
  document.querySelectorAll("[data-editor-command], .insert-at-cursor, .insert-selected-image, [data-copy-image]").forEach((button) => {
    button.addEventListener("mousedown", (event) => event.preventDefault());
    button.addEventListener("pointerdown", (event) => event.preventDefault());
  });
  document.querySelectorAll("[data-editor-command]").forEach((button) => button.addEventListener("click", () => runCommand(button.dataset.editorCommand)));
  document.querySelectorAll("[data-view-mode]").forEach((button) => button.addEventListener("click", () => setViewMode(button.dataset.viewMode)));
  document.querySelector("#toggle-markdown-help")?.addEventListener("click", (event) => {
    const help = document.querySelector("#markdown-help");
    if (!help) return;
    const expanded = help.hidden;
    help.hidden = !expanded;
    event.currentTarget.setAttribute("aria-expanded", expanded ? "true" : "false");
    event.currentTarget.textContent = expanded ? "收起 Markdown 小抄" : "打开 Markdown 小抄";
  });

  editor.addEventListener("keydown", (event) => {
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "b") {
      event.preventDefault();
      runCommand("bold");
    } else if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "i") {
      event.preventDefault();
      runCommand("italic");
    } else if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "z") {
      event.preventDefault();
      event.shiftKey ? redo() : undo();
    } else if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "y") {
      event.preventDefault();
      redo();
    } else if (event.key === "Tab") {
      event.preventDefault();
      replaceSelection("  ", 2, 2);
    }
  });
  editor.addEventListener("input", () => {
    pushHistory();
    refreshPreview();
  });
  titleInput?.addEventListener("input", () => { updateDirtyState(); saveLocalDraft(); scheduleAutosave(); });
  statusInput?.addEventListener("change", () => { updateDirtyState(); saveLocalDraft(); scheduleAutosave(); });

  document.querySelectorAll(".insert-at-cursor").forEach((button) => {
    button.addEventListener("click", () => {
      const markdown = imageData[button.dataset.imageId];
      if (!markdown || editor.value.includes(markdown.match(/\/media\/[^)]+/)?.[0] || "__missing__")) return markImageInserted(button.dataset.imageId);
      insertAtCursor(markdown);
      markImageInserted(button.dataset.imageId);
    });
  });
  document.querySelectorAll(".insert-selected-image").forEach((button) => {
    button.addEventListener("click", () => {
      const markdown = imageData[button.dataset.imageId];
      const select = document.querySelector(`.image-position-select[data-image-id="${CSS.escape(button.dataset.imageId)}"]`);
      if (!markdown || !select) return;
      const mediaPath = markdown.match(/\/media\/[^)]+/)?.[0];
      if (mediaPath && editor.value.includes(mediaPath)) return markImageInserted(button.dataset.imageId);
      insertAtPosition(markdown, select.value);
      markImageInserted(button.dataset.imageId);
    });
  });
  document.querySelectorAll("[data-copy-image]").forEach((button) => {
    button.addEventListener("click", async () => {
      const markdown = imageData[button.dataset.copyImage];
      if (!markdown) return;
      const original = button.textContent;
      button.textContent = (await copyText(markdown)) ? "已复制" : "复制失败";
      setTimeout(() => { button.textContent = original; }, 1600);
    });
  });

  const draftBanner = document.querySelector("#local-draft-banner");
  const draftMeta = document.querySelector("#local-draft-meta");
  let storedDraft = null;
  try {
    storedDraft = JSON.parse(localStorage.getItem(draftKey) || "null");
  } catch (_) {
    storedDraft = null;
  }
  if (storedDraft && typeof storedDraft.content === "string" && !sameState(storedDraft, initialState)) {
    if (draftMeta) draftMeta.textContent = `保存于 ${formatTime(storedDraft.savedAt)}，只保存在当前浏览器。`;
    if (draftBanner) draftBanner.hidden = false;
  }
  document.querySelector("#restore-local-draft")?.addEventListener("click", () => {
    if (!storedDraft) return;
    if (titleInput) titleInput.value = storedDraft.title || "";
    if (statusInput) statusInput.value = storedDraft.status || "draft";
    applyEditorValue(storedDraft.content || "");
    if (draftBanner) draftBanner.hidden = true;
  });
  document.querySelector("#dismiss-local-draft")?.addEventListener("click", () => {
    try { localStorage.removeItem(draftKey); } catch (_) { /* ignore */ }
    if (draftBanner) draftBanner.hidden = true;
  });
  form?.addEventListener("submit", () => {
    try { localStorage.removeItem(draftKey); } catch (_) { /* ignore */ }
    hasUnsavedChanges = false;
  });

  // A short idle save plus a visible periodic check keeps long writing sessions safe.
  window.setInterval(() => requestAutosave("定时"), 30_000);
  updateStats();
  refreshHeadingOptions();
  updateDirtyState();
})();
