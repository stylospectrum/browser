window.div = window.document.querySelectorAll("div")[0];

window.fade_in = function () {
  window.requestAnimationFrame(function () {
    window.div.style = "opacity: 0.999";
  });
};

window.fade_out = function () {
  window.requestAnimationFrame(function () {
    window.div.style = "opacity: 0.1";
  });
};

window.document
  .querySelectorAll("button")[0]
  .addEventListener("click", window.fade_out);
window.document
  .querySelectorAll("button")[1]
  .addEventListener("click", window.fade_in);
