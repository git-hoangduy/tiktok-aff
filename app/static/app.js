// Upload preview + drag & drop
function initUpload() {
  const input = document.getElementById("imgInput");
  const drop = document.getElementById("drop");
  const box = document.getElementById("previews");
  const show = () => {
    box.innerHTML = "";
    const files = [...input.files].slice(0, 10);
    files.forEach((f, i) => {
      const fig = document.createElement("figure");
      const img = document.createElement("img");
      img.src = URL.createObjectURL(f);
      img.alt = f.name;
      // HEIC/TIFF can't be previewed by most browsers, but the server still reads them
      img.onerror = () => { img.replaceWith(Object.assign(document.createElement("div"), { className: "ph", textContent: f.name.split(".").pop().toUpperCase() })); };
      fig.appendChild(img);
      fig.appendChild(document.createTextNode("Ảnh " + (i + 1)));
      box.appendChild(fig);
    });
    if (input.files.length > 10) alert("Tối đa 10 ảnh — chỉ 10 ảnh đầu được dùng.");
  };
  input.addEventListener("change", show);
  ["dragenter", "dragover"].forEach(e => drop.addEventListener(e, ev => { ev.preventDefault(); drop.classList.add("over"); }));
  ["dragleave", "drop"].forEach(e => drop.addEventListener(e, ev => { ev.preventDefault(); drop.classList.remove("over"); }));
  drop.addEventListener("drop", ev => { input.files = ev.dataTransfer.files; show(); });
  document.getElementById("createForm").addEventListener("submit", () => {
    const b = document.getElementById("goBtn"); b.disabled = true; b.textContent = "Đang tải lên...";
  });
}

const LABEL = { queued: "Đang chờ", running: "Đang xử lý", done: "Hoàn thành", error: "Lỗi", ready: "Sẵn sàng", new: "Mới" };
const badge = s => `<span class="badge st-${s}">${LABEL[s] || s}</span>`;
const busy = s => s === "queued" || s === "running";

// Product page: refresh when analysis/scripts/videos change
function pollProduct(pid, nScripts, nVideos) {
  const bar = document.getElementById("pstatus");
  let last = null;
  const tick = async () => {
    try {
      const d = await (await fetch(`/api/products/${pid}`)).json();
      bar.innerHTML = `${badge(d.status)} <span>${d.message || ""}</span>`;
      const vs = d.videos.map(v => v.status);
      const anyBusy = busy(d.status) || vs.some(busy);
      // reload the page whenever something finished (new scripts, a video done/failed)
      const key = JSON.stringify([busy(d.status), d.scripts, vs.filter(s => !busy(s)).length, d.videos.length]);
      const vbox = document.getElementById("pvideos");
      if (vbox && d.videos.length) {
        vbox.innerHTML = d.videos.map(v => `<tr><td><a href="/videos/${v.id}">Video #${v.id}</a></td><td>${badge(v.status)}</td><td>${v.progress}%</td><td class="muted">${(v.message || "").slice(0, 120)}</td></tr>`).join("");
      }
      if (last !== null && key !== last) { location.reload(); return; }
      last = key;
      if (anyBusy) setTimeout(tick, 2500);
    } catch (e) { setTimeout(tick, 5000); }
  };
  setTimeout(tick, 1500);
}

// Video page: progress bar, reload when finished
function pollVideo(vid, status) {
  if (!busy(status)) return;
  const bar = document.getElementById("vstatus");
  const tick = async () => {
    try {
      const d = await (await fetch(`/api/videos/${vid}`)).json();
      bar.querySelector(".badge").outerHTML = badge(d.status);
      bar.querySelector(".progress div").style.width = d.progress + "%";
      document.getElementById("vmsg").textContent = d.message || "";
      if (busy(d.status)) setTimeout(tick, 2000); else location.reload();
    } catch (e) { setTimeout(tick, 4000); }
  };
  setTimeout(tick, 1200);
}
