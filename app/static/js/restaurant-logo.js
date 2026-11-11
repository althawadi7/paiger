(function () {
  "use strict";

  function initialFrom(el) {
    var raw = (el.getAttribute("data-initial") || "R").trim();
    return raw.charAt(0) || "R";
  }

  function ensureSpin(el) {
    var spin = el.querySelector(".r-logo__spin");
    if (!spin) {
      spin = document.createElement("span");
      spin.className = "r-logo__spin";
      spin.setAttribute("aria-hidden", "true");
      spin.hidden = true;
      el.insertBefore(spin, el.firstChild);
    }
    return spin;
  }

  function setLoading(el, on) {
    if (!el || !el.classList.contains("r-logo")) return;
    el.classList.toggle("is-loading", !!on);
    var spin = ensureSpin(el);
    spin.hidden = !on;
  }

  function showFallback(el) {
    if (!el) return;
    el.classList.remove("has-img");
    var img = el.querySelector(".r-logo__img");
    if (img) img.remove();
    var init = el.querySelector(".r-logo__initial");
    if (!init) {
      init = document.createElement("span");
      init.className = "r-logo__initial";
      init.setAttribute("aria-hidden", "true");
      el.appendChild(init);
    }
    init.textContent = initialFrom(el);
    setLoading(el, false);
  }

  function wireImg(img) {
    var el = img.closest(".r-logo");
    if (!el) return;
    if (img.complete && img.naturalWidth > 0) {
      img.classList.remove("is-loading");
      setLoading(el, false);
      return;
    }
    img.classList.add("is-loading");
    setLoading(el, true);
    img.addEventListener(
      "load",
      function () {
        img.classList.remove("is-loading");
        setLoading(el, false);
      },
      { once: true }
    );
    img.addEventListener("error", function () { showFallback(el); }, { once: true });
  }

  function setSlotImage(el, url) {
    if (!el) return;
    if (!url) {
      showFallback(el);
      return;
    }
    el.classList.add("has-img");
    var init = el.querySelector(".r-logo__initial");
    if (init) init.remove();
    var img = el.querySelector(".r-logo__img");
    if (!img) {
      img = document.createElement("img");
      img.className = "r-logo__img";
      img.alt = "";
      el.appendChild(img);
    }
    img.classList.add("is-loading");
    setLoading(el, true);
    img.onload = function () {
      img.classList.remove("is-loading");
      setLoading(el, false);
    };
    img.onerror = function () { showFallback(el); };
    img.src = url;
    wireImg(img);
  }

  function setUploadBusy(btn, busy) {
    if (!btn) return;
    btn.disabled = !!busy;
    btn.setAttribute("aria-busy", busy ? "true" : "false");
  }

  document.querySelectorAll(".r-logo__img").forEach(wireImg);

  window.paigerRestaurantLogo = {
    setSlotImage: setSlotImage,
    setLoading: setLoading,
    showFallback: showFallback,
    wireImg: wireImg,
    setUploadBusy: setUploadBusy,
  };
})();
