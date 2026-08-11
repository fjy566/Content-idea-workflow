(() => {
  const overlay = document.querySelector("#operation-overlay");
  const title = document.querySelector("#operation-title");
  const detail = document.querySelector("#operation-detail");
  const seconds = document.querySelector("#operation-seconds");
  if (!overlay) return;

  let interval;
  const reset = () => {
    clearInterval(interval);
    overlay.hidden = true;
    document.body.removeAttribute("aria-busy");
    document.querySelectorAll("form[data-submitting='true']").forEach((form) => {
      form.dataset.submitting = "false";
      form.querySelectorAll("button").forEach((button) => {
        button.disabled = button.dataset.wasDisabled === "true";
        if (button.dataset.originalText) button.textContent = button.dataset.originalText;
      });
    });
  };
  window.addEventListener("pageshow", reset);

  document.querySelectorAll("form[data-loading]").forEach((form) => {
    form.addEventListener("submit", (event) => {
      if (form.dataset.submitting === "true") {
        event.preventDefault();
        return;
      }
      event.preventDefault();
      form.dataset.submitting = "true";
      const submitter = event.submitter;
      if (submitter?.name) {
        const mirror = document.createElement("input");
        mirror.type = "hidden";
        mirror.name = submitter.name;
        mirror.value = submitter.value;
        form.appendChild(mirror);
      }
      let elapsed = 0;
      title.textContent = form.dataset.loadingTitle || "正在处理，请稍候";
      detail.textContent = form.dataset.loadingDetail || "操作可能需要几十秒，请不要重复点击或关闭页面。";
      seconds.textContent = "0";
      overlay.hidden = false;
      document.body.setAttribute("aria-busy", "true");
      form.querySelectorAll("button").forEach((button) => {
        button.dataset.wasDisabled = String(button.disabled);
        button.dataset.originalText = button.textContent;
        button.disabled = true;
      });
      if (submitter) submitter.textContent = submitter.dataset.loadingText || "正在处理…";
      interval = window.setInterval(() => {
        elapsed += 1;
        seconds.textContent = String(elapsed);
      }, 1000);
      // Only a button with explicit form* attributes may override its form.
      // Reading submitter.formMethod here returns the browser default (GET)
      // for an ordinary button and used to turn article POSTs into GETs.
      const targetAction = submitter?.getAttribute("formaction");
      const targetMethod = submitter?.getAttribute("formmethod");
      window.setTimeout(() => {
        if (targetAction) form.action = targetAction;
        if (targetMethod) form.method = targetMethod;
        HTMLFormElement.prototype.submit.call(form);
      }, 80);
    });
  });
})();
