(() => {
  const card = document.querySelector("#training-progress-card");
  if (!card) return;

  const status = document.querySelector("#training-status");
  const progress = document.querySelector("#training-progress-bar");
  const percent = document.querySelector("#training-progress-percent");
  const phase = document.querySelector("#training-phase");
  const message = document.querySelector("#training-message");
  const requestedDevice = document.querySelector("#training-requested-device");
  const actualDevice = document.querySelector("#training-actual-device");
  const sampleCount = document.querySelector("#training-sample-count");
  const algorithm = document.querySelector("#training-algorithm");
  const liveMetrics = document.querySelector("#training-live-metrics");
  const error = document.querySelector("#training-error");
  const statusUrl = card.dataset.statusUrl;
  const statusLabels = {
    pending: "排队中",
    running: "训练中",
    succeeded: "已完成",
    failed: "失败",
  };
  const phaseLabels = {
    starting: "启动任务",
    preparing: "整理数据",
    data_ready: "数据准备完成",
    split: "划分训练集",
    training: "训练模型",
    fallback: "切换 CPU",
    evaluating: "计算验证指标",
    evaluation_done: "指标计算完成",
    scoring: "应用模型分",
    refreshing: "刷新热点排序",
    completed: "训练完成",
    failed: "训练失败",
  };

  const renderMetrics = (metrics) => {
    if (!metrics || typeof metrics !== "object") return;
    const values = [
      ["验证 MAE", metrics.validation_mae],
      ["验证 RMSE", metrics.validation_rmse],
      ["验证 R²", metrics.validation_r2],
      ["模型评分设备", metrics.score_device || metrics.prediction_device],
      ["批量评分", metrics.score_seconds == null ? null : `${metrics.score_seconds} 秒`],
      ["训练耗时", metrics.training_seconds == null ? null : `${metrics.training_seconds} 秒`],
    ].filter((item) => item[1] !== null && item[1] !== undefined);
    liveMetrics.textContent = values.map((item) => `${item[0]}：${item[1]}`).join("　");
  };

  const render = (data) => {
    const value = Math.max(0, Math.min(100, Number(data.progress || 0)));
    progress.value = value;
    percent.textContent = `${Math.round(value)}%`;
    phase.textContent = phaseLabels[data.phase] || data.phase || "处理中";
    message.textContent = data.message || "正在处理…";
    status.textContent = statusLabels[data.status] || data.status || "处理中";
    status.className = `status ${data.status === "succeeded" ? "ok" : data.status === "failed" ? "error" : ""}`;
    if (data.requested_device) requestedDevice.textContent = data.requested_device.toUpperCase();
    if (data.actual_device) actualDevice.textContent = data.actual_device;
    if (data.sample_count) sampleCount.textContent = data.sample_count;
    if (data.metrics?.algorithm) algorithm.textContent = data.metrics.algorithm;
    renderMetrics(data.metrics);
    if (data.error) {
      error.hidden = false;
      error.textContent = data.error;
    }
  };

  let timer;
  const poll = async () => {
    try {
      const response = await fetch(statusUrl, { headers: { Accept: "application/json" } });
      if (!response.ok) throw new Error(`status ${response.status}`);
      const data = await response.json();
      render(data);
      if (data.status === "succeeded") {
        window.clearInterval(timer);
        window.setTimeout(() => {
          window.location.replace(`/recommender?message=${encodeURIComponent("训练完成：热点排序已更新")}`);
        }, 1000);
      } else if (data.status === "failed") {
        window.clearInterval(timer);
      }
    } catch (pollError) {
      message.textContent = "暂时无法读取训练进度，正在重试…";
    }
  };

  poll();
  timer = window.setInterval(poll, 1000);
})();
