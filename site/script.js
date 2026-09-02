(() => {
  const root = document.documentElement;
  const header = document.querySelector("[data-header]");
  const menuButton = document.querySelector("[data-menu-button]");
  const navigation = document.querySelector("[data-site-nav]");
  const themeSelect = document.querySelector("[data-theme-select]");
  const languageSelect = document.querySelector("[data-language-select]");
  const themeMedia = window.matchMedia("(prefers-color-scheme: dark)");
  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
  const supportedLocales = new Set(["zh-CN", "zh-TW", "en", "ja", "ko"]);
  let languageRequest = 0;

  const readPreference = (key, fallback) => {
    try { return localStorage.getItem(key) || fallback; } catch { return fallback; }
  };

  const savePreference = (key, value) => {
    try { localStorage.setItem(key, value); } catch {
      // Strict privacy modes can disable storage; keep the current in-memory selection.
    }
  };

  const resolveTheme = (preference) => preference === "light" || preference === "dark"
    ? preference
    : (themeMedia.matches ? "dark" : "light");

  const applyTheme = (preference) => {
    const theme = resolveTheme(preference);
    root.dataset.theme = theme;
    root.style.colorScheme = theme;
    if (themeSelect) themeSelect.value = preference;
    document.querySelector('meta[name="theme-color"]')?.setAttribute("content", theme === "dark" ? "#0b1320" : "#f7f9fc");
  };

  const normalizeLocale = (language) => {
    const value = String(language || "").replace("_", "-");
    const lower = value.toLowerCase();
    if (lower.startsWith("zh")) return /(?:hant|tw|hk|mo)/i.test(value) ? "zh-TW" : "zh-CN";
    if (lower.startsWith("ja")) return "ja";
    if (lower.startsWith("ko")) return "ko";
    if (lower.startsWith("en")) return "en";
    return "zh-CN";
  };

  const resolveLocale = (preference) => {
    if (supportedLocales.has(preference)) return preference;
    for (const language of navigator.languages || [navigator.language]) {
      const locale = normalizeLocale(language);
      if (supportedLocales.has(locale)) return locale;
    }
    return "zh-CN";
  };

  const setTranslatedAttribute = (selector, attribute, translations) => {
    const dataAttribute = selector.slice(1, -1);
    document.querySelectorAll(selector).forEach((element) => {
      const key = element.getAttribute(dataAttribute);
      if (key && typeof translations[key] === "string") element.setAttribute(attribute, translations[key]);
    });
  };

  const preloadLocalizedImages = (translations) => {
    const sources = new Set(
      [...document.querySelectorAll("[data-i18n-src]")]
        .map((image) => translations[image.dataset.i18nSrc])
        .filter((source) => typeof source === "string" && source),
    );
    return Promise.all([...sources].map((source) => new Promise((resolve, reject) => {
      const image = new Image();
      image.onload = resolve;
      image.onerror = reject;
      image.src = new URL(source, document.baseURI).href;
    })));
  };

  const applyLanguage = async (preference) => {
    const request = ++languageRequest;
    const locale = resolveLocale(preference);
    const response = await fetch(new URL(`./locales/${locale}.json`, document.baseURI));
    if (!response.ok) throw new Error(`Unable to load locale ${locale}: ${response.status}`);
    const translations = await response.json();
    // Load the matching product captures first so copy and screenshots switch atomically.
    await preloadLocalizedImages(translations);
    if (request !== languageRequest) return;

    document.querySelectorAll("[data-i18n]").forEach((element) => {
      const key = element.dataset.i18n;
      if (typeof translations[key] === "string") element.textContent = translations[key];
    });
    document.querySelectorAll("[data-i18n-html]").forEach((element) => {
      const key = element.dataset.i18nHtml;
      if (typeof translations[key] === "string") element.innerHTML = translations[key];
    });
    setTranslatedAttribute("[data-i18n-aria]", "aria-label", translations);
    setTranslatedAttribute("[data-i18n-alt]", "alt", translations);
    document.querySelectorAll("[data-i18n-src]").forEach((image) => {
      const source = translations[image.dataset.i18nSrc];
      if (typeof source !== "string" || !source) return;
      image.setAttribute("src", source);
      image.closest("a")?.setAttribute("href", source);
    });

    root.lang = locale;
    if (languageSelect) languageSelect.value = preference;
    document.title = translations.meta_title || document.title;
    document.querySelector('meta[name="description"]')?.setAttribute("content", translations.meta_description || "");
    document.querySelector('meta[property="og:title"]')?.setAttribute("content", translations.meta_title || "");
    document.querySelector('meta[property="og:description"]')?.setAttribute("content", translations.meta_description || "");
    document.querySelector('meta[name="twitter:title"]')?.setAttribute("content", translations.meta_title || "");
    document.querySelector('meta[name="twitter:description"]')?.setAttribute("content", translations.meta_description || "");
    document.querySelector('meta[property="og:locale"]')?.setAttribute("content", locale.replace("-", "_"));
  };

  const setMenuState = (open) => {
    if (!menuButton || !navigation) return;
    menuButton.setAttribute("aria-expanded", String(open));
    navigation.dataset.open = String(open);
    document.body.classList.toggle("menu-open", open);
  };

  const themePreference = readPreference("tokenmeter-site-theme", "auto");
  const languagePreference = readPreference("tokenmeter-site-language", "auto");
  applyTheme(themePreference);
  applyLanguage(languagePreference).catch(() => {
    if (languageSelect) languageSelect.value = languagePreference;
  });

  themeSelect?.addEventListener("change", () => {
    savePreference("tokenmeter-site-theme", themeSelect.value);
    applyTheme(themeSelect.value);
  });
  languageSelect?.addEventListener("change", () => {
    savePreference("tokenmeter-site-language", languageSelect.value);
    applyLanguage(languageSelect.value).catch(() => {});
  });
  themeMedia.addEventListener("change", () => {
    if (themeSelect?.value === "auto") applyTheme("auto");
  });
  window.addEventListener("languagechange", () => {
    if (languageSelect?.value === "auto") applyLanguage("auto").catch(() => {});
  });

  menuButton?.addEventListener("click", () => setMenuState(menuButton.getAttribute("aria-expanded") !== "true"));
  navigation?.addEventListener("click", (event) => {
    if (event.target.closest("a")) setMenuState(false);
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && menuButton?.getAttribute("aria-expanded") === "true") {
      setMenuState(false);
      menuButton.focus();
    }
  });
  window.addEventListener("resize", () => {
    if (window.innerWidth > 900) setMenuState(false);
  });

  const updateHeader = () => header?.classList.toggle("is-scrolled", window.scrollY > 18);
  updateHeader();
  window.addEventListener("scroll", updateHeader, { passive: true });

  const revealElements = [...document.querySelectorAll("[data-reveal]")];
  if (reducedMotion.matches || !("IntersectionObserver" in window)) {
    revealElements.forEach((element) => element.classList.add("is-visible"));
  } else {
    const revealObserver = new IntersectionObserver((entries) => {
      entries.forEach((entry) => {
        if (!entry.isIntersecting) return;
        entry.target.classList.add("is-visible");
        revealObserver.unobserve(entry.target);
      });
    }, { rootMargin: "0px 0px -9%", threshold: 0.08 });
    revealElements.forEach((element) => revealObserver.observe(element));
  }

  const navLinks = [...document.querySelectorAll("[data-nav-link]")];
  const navTargets = navLinks.map((link) => document.querySelector(link.getAttribute("href"))).filter(Boolean);
  if ("IntersectionObserver" in window && navTargets.length) {
    const navObserver = new IntersectionObserver((entries) => {
      const current = entries.filter((entry) => entry.isIntersecting).sort((a, b) => b.intersectionRatio - a.intersectionRatio)[0];
      if (!current) return;
      navLinks.forEach((link) => link.classList.toggle("is-active", link.getAttribute("href") === `#${current.target.id}`));
    }, { rootMargin: "-28% 0px -62%", threshold: [0, 0.15, 0.5] });
    navTargets.forEach((section) => navObserver.observe(section));
  }

  const animateNumber = (element) => {
    if (element.dataset.counted === "true") return;
    element.dataset.counted = "true";
    const target = Number(element.dataset.counter);
    const decimals = Number(element.dataset.decimals || 0);
    if (!Number.isFinite(target) || reducedMotion.matches) {
      element.textContent = Number.isFinite(target) ? target.toFixed(decimals) : element.textContent;
      return;
    }
    const start = performance.now();
    const duration = 850;
    const tick = (time) => {
      const progress = Math.min((time - start) / duration, 1);
      const eased = 1 - Math.pow(1 - progress, 3);
      element.textContent = (target * eased).toFixed(decimals);
      if (progress < 1) requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  };

  const dataElements = [...document.querySelectorAll("[data-counter]")];
  if ("IntersectionObserver" in window) {
    const dataObserver = new IntersectionObserver((entries) => {
      entries.forEach((entry) => {
        if (!entry.isIntersecting) return;
        animateNumber(entry.target);
        dataObserver.unobserve(entry.target);
      });
    }, { threshold: 0.35 });
    dataElements.forEach((element) => dataObserver.observe(element));
  } else {
    dataElements.forEach(animateNumber);
  }

  const tabs = [...document.querySelectorAll("[data-demo-tab]")];
  const panels = [...document.querySelectorAll("[data-demo-panel]")];
  let switchTimer = 0;

  const restartPanelData = (panel) => {
    panel.querySelectorAll("[data-counter]").forEach((element) => {
      element.dataset.counted = "false";
      element.textContent = "0";
      animateNumber(element);
    });
  };

  const selectTab = (tab, focus = false) => {
    const key = tab.dataset.demoTab;
    const nextPanel = panels.find((panel) => panel.dataset.demoPanel === key);
    const currentPanel = panels.find((panel) => !panel.hidden);
    if (!nextPanel || nextPanel === currentPanel) {
      if (focus) tab.focus();
      return;
    }
    clearTimeout(switchTimer);
    tabs.forEach((item) => {
      const selected = item === tab;
      item.setAttribute("aria-selected", String(selected));
      item.tabIndex = selected ? 0 : -1;
    });
    currentPanel?.classList.add("is-leaving");
    const completeSwitch = () => {
      panels.forEach((panel) => {
        panel.hidden = panel !== nextPanel;
        panel.classList.remove("is-active", "is-entering", "is-leaving");
      });
      nextPanel.hidden = false;
      nextPanel.classList.add("is-active", "is-entering");
      restartPanelData(nextPanel);
      window.setTimeout(() => nextPanel.classList.remove("is-entering"), reducedMotion.matches ? 0 : 400);
    };
    // Exit remains shorter than entry; final state never depends on animationend firing.
    switchTimer = window.setTimeout(completeSwitch, reducedMotion.matches ? 0 : 120);
    if (focus) tab.focus();
  };

  tabs.forEach((tab, index) => {
    tab.addEventListener("click", () => selectTab(tab));
    tab.addEventListener("keydown", (event) => {
      if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
      event.preventDefault();
      let nextIndex = index;
      if (event.key === "ArrowRight") nextIndex = (index + 1) % tabs.length;
      if (event.key === "ArrowLeft") nextIndex = (index - 1 + tabs.length) % tabs.length;
      if (event.key === "Home") nextIndex = 0;
      if (event.key === "End") nextIndex = tabs.length - 1;
      selectTab(tabs[nextIndex], true);
    });
  });
})();
