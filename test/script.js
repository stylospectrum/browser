var div = document.querySelectorAll("div")[0];

function fade_in() {
  requestAnimationFrame(function () {
    div.style = "opacity:0.999";
  });
}

function fade_out() {
  requestAnimationFrame(function () {
    div.style = "opacity:0.1";
  });
}

document.querySelectorAll("button")[0].addEventListener("click", fade_out);
document.querySelectorAll("button")[1].addEventListener("click", fade_in);
