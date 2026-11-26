"use strict";
const picker = document.getElementById("station-route-picker");
if (picker) {
  picker.hidden = false;
  document.getElementById("append-route-station").addEventListener("click", () => {
    const station = document.getElementById("route-station");
    const detail = document.getElementById("route-detail");
    const route = document.querySelector("textarea[name=route]");
    if (!station.value) { station.focus(); return; }
    route.value = (route.value.trim() ? route.value.trim() + "\n" : "") + station.value + " | " + detail.value.trim();
    detail.value = "";
    route.focus();
  });
}
