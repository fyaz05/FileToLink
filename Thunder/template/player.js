/**
 * Player page chrome: theme, external-player drawer, clipboard and download
 * feedback. Playback itself belongs to <movi-player>.
 */
(() => {
  "use strict";

  const config = window.__CINEMA_CONFIG__ || {};
  const streamUrl = config.src || "";
  const mimeType = config.mimeType || "";
  const fileName = config.fileName || document.title || "media";

  const THEME_STORAGE_KEY = "theme-preference";
  const TOAST_VISIBLE_MS = 2000;

  const player = document.getElementById("player");
  const copyBtn = document.getElementById("copyBtn");
  const downloadBtn = document.getElementById("downloadBtn");
  const playersToggle = document.getElementById("playersToggle");
  const drawerPanel = document.getElementById("drawerPanel");
  const toastStack = document.getElementById("toastStack");
  const themeToggle = document.getElementById("themeToggle");
  const androidGroup = document.getElementById("androidGroup");
  const iosGroup = document.getElementById("iosGroup");
  const desktopGroup = document.getElementById("desktopGroup");

  const prefersReducedMotion = () =>
    window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  const showToast = (message, kind = "default") => {
    if (!toastStack) return;
    const toast = document.createElement("div");
    toast.className = `toast ${kind}`;
    toast.textContent = message;
    toastStack.appendChild(toast);
    setTimeout(() => {
      toast.style.animation = "toastExit 0.3s ease forwards";
      setTimeout(() => toast.remove(), 300);
    }, TOAST_VISIBLE_MS);
  };

  // ── Theme ────────────────────────────────────────────────────────────────
  const systemTheme = () =>
    window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";

  const readStoredTheme = () => {
    try {
      return localStorage.getItem(THEME_STORAGE_KEY);
    } catch {
      return null;
    }
  };

  const applyTheme = (theme, persist = false) => {
    document.documentElement.setAttribute("data-theme", theme);
    // The player styles its own shadow DOM, so it needs the theme passed in.
    player?.setAttribute("theme", theme);
    // The icon is the only visual cue, so the name carries the state too.
    themeToggle?.setAttribute(
      "aria-label",
      theme === "dark" ? "Switch to light theme" : "Switch to dark theme",
    );
    if (!persist) return;
    try {
      localStorage.setItem(THEME_STORAGE_KEY, theme);
    } catch {
      /* storage unavailable; the in-page theme still applies */
    }
  };

  const initTheme = () => applyTheme(readStoredTheme() || systemTheme());

  const toggleTheme = () => {
    const current = document.documentElement.getAttribute("data-theme") || "dark";
    applyTheme(current === "dark" ? "light" : "dark", true);
  };

  const lightModeQuery = window.matchMedia("(prefers-color-scheme: light)");
  const onSystemThemeChange = (event) => {
    // Only follow the OS when the visitor has not chosen explicitly.
    if (!readStoredTheme()) applyTheme(event.matches ? "light" : "dark");
  };

  if (typeof lightModeQuery.addEventListener === "function") {
    lightModeQuery.addEventListener("change", onSystemThemeChange);
  } else if (typeof lightModeQuery.addListener === "function") {
    lightModeQuery.addListener(onSystemThemeChange);
  }
  themeToggle?.addEventListener("click", toggleTheme);

  // ── Playback failures ────────────────────────────────────────────────────
  // fallback="native" keeps a working surface, so this is a notice.
  player?.addEventListener("nativefallback", () => {
    showToast("This browser handles the file natively; some tracks may be unavailable.");
  });
  player?.addEventListener("error", () => {
    showToast("Playback failed. Try an external player below.", "error");
  });

  // ── Track buttons ────────────────────────────────────────────────────────
  /**
   * movi hides its track buttons in a tray below 721px, and a control anchored
   * `before: "cc"` goes into that tray too. Anchoring before `more` keeps these
   * in the always-visible part of the bar. They open movi's own menus, so the
   * track lists cannot drift from what the player knows.
   */
  const TRACK_BUTTONS = [
    {
      id: "thunder-audio-track",
      label: "Audio track",
      title: "Audio track",
      selector: ".movi-audio-track-btn",
      icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M4 14v-4a2 2 0 0 1 2-2h2l4-4v16l-4-4H6a2 2 0 0 1-2-2z"/><path d="M16 8.5a5 5 0 0 1 0 7"/><path d="M19 6a9 9 0 0 1 0 12"/></svg>',
    },
    {
      id: "thunder-subtitles",
      label: "Subtitles",
      title: "Subtitles and captions",
      selector: ".movi-subtitle-track-btn",
      icon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><rect x="3" y="5" width="18" height="14" rx="2"/><path d="M7 13h4M13 13h4M7 16h6"/></svg>',
    },
  ];

  const addTrackButtons = () => {
    if (typeof player?.addControl !== "function") return;
    const root = player.shadowRoot;
    if (!root) return;

    for (const spec of TRACK_BUTTONS) {
      // Nothing to open if the file has no track of this kind.
      if (!root.querySelector(spec.selector)) continue;
      try {
        player.addControl({
          id: spec.id,
          label: spec.label,
          title: spec.title,
          icon: spec.icon,
          before: "more",
          placement: "bar",
          onSelect: () => root.querySelector(spec.selector)?.click(),
        });
      } catch {
        /* a control that will not register must not break the page */
      }
    }
  };

  // The player builds its chrome once the source is known, so wait for it.
  if (player) {
    if (customElements.get("movi-player")) addTrackButtons();
    else customElements.whenDefined("movi-player").then(() => setTimeout(addTrackButtons, 0));
  }

  // ── External players ─────────────────────────────────────────────────────
  /** Removes the player groups (and OS-specific links) that do not apply here. */
  const prunePlayerGroups = () => {
    const userAgent = navigator.userAgent;
    const isAndroid = /android/i.test(userAgent);
    const isIOS = /iphone|ipad|ipod/i.test(userAgent) ||
      (/macintosh/i.test(userAgent) && navigator.maxTouchPoints > 1);
    const isMac = /macintosh|mac os x/i.test(userAgent) && !isIOS;
    const isWindows = /windows/i.test(userAgent);

    if (isAndroid) {
      iosGroup?.remove();
      desktopGroup?.remove();
      return;
    }
    if (isIOS) {
      androidGroup?.remove();
      desktopGroup?.remove();
      return;
    }
    androidGroup?.remove();
    iosGroup?.remove();
    desktopGroup?.querySelectorAll(".player-link").forEach((link) => {
      const os = link.dataset.os;
      if ((os === "mac" && !isMac) || (os === "windows" && !isWindows)) link.remove();
    });
  };

  /** The Android intent `type=` value, taken from the server's classification. */
  const intentType = (mime) => {
    if (config.isAudio) return "audio/*";
    if (config.isVideo) return "video/*";
    const normalized = String(mime || "").trim().toLowerCase();
    if (normalized.startsWith("audio/")) return "audio/*";
    if (normalized.startsWith("video/")) return "video/*";
    return "";
  };

  const absoluteStreamUrl = () => {
    try {
      return new URL(streamUrl, window.location.href).href;
    } catch {
      return streamUrl;
    }
  };

  /** Splits a URL into its scheme and a scheme-less form for intent:// URIs. */
  const splitUrl = (url) => {
    const parsed = new URL(url, window.location.href);
    return {
      scheme: parsed.protocol.replace(":", "") || "https",
      noScheme: parsed.href.replace(/^[a-z][a-z0-9+.-]*:\/\//i, "").replace(/#/g, "%23"),
    };
  };

  const buildAndroidIntent = ({ packageName, title = fileName, fallbackURL }) => {
    const { scheme, noScheme } = splitUrl(absoluteStreamUrl());
    const type = intentType(mimeType);
    const parts = [
      `intent://${noScheme}#Intent`,
      `scheme=${encodeURIComponent(scheme)}`,
      "action=android.intent.action.VIEW",
    ];
    if (type) parts.push(`type=${type}`);
    if (packageName) parts.push(`package=${packageName}`);
    if (title) parts.push(`S.title=${encodeURIComponent(title)}`);
    if (fallbackURL) parts.push(`S.browser_fallback_url=${encodeURIComponent(fallbackURL)}`);
    parts.push("end");
    return parts.join(";");
  };

  const playStoreUrl = (packageName) =>
    `https://play.google.com/store/apps/details?id=${encodeURIComponent(packageName)}`;

  const ANDROID_PACKAGES = {
    "android-vlc": "org.videolan.vlc",
    "android-vlc-beta": "org.videolan.vlc.debug",
    "android-mx": "com.mxtech.videoplayer.ad",
    "android-mx-pro": "com.mxtech.videoplayer.pro",
    "android-splayer": "com.young.simple.player",
    "android-next": "dev.anilbeesetti.nextplayer",
    "android-nova": "org.courville.nova",
    "android-mpv": "is.xyz.mpv",
    "android-just": "com.brouken.player",
    "android-nplayer": "com.newin.nplayer.pro",
  };

  /** Resolves a `data-player` key to the deep link that opens it, or "". */
  const buildPlayerHref = (playerKey) => {
    if (!streamUrl) return "";
    const absolute = absoluteStreamUrl();
    const encoded = encodeURIComponent(absolute);

    if (playerKey === "android-chooser") return buildAndroidIntent({ packageName: "", title: fileName });

    const androidPackage = ANDROID_PACKAGES[playerKey];
    if (androidPackage) {
      return buildAndroidIntent({
        packageName: androidPackage,
        fallbackURL: playStoreUrl(androidPackage),
      });
    }

    const iosHrefs = {
      "ios-vlc": `vlc-x-callback://x-callback-url/stream?url=${encoded}`,
      "ios-infuse": `infuse://x-callback-url/play?url=${encoded}`,
      "ios-nplayer": `nplayer://weblink?url=${encoded}`,
      "ios-outplayer": `outplayer://x-callback-url/play?url=${encoded}`,
      "ios-oplayer": `oplayer://x-callback-url/play?url=${encoded}`,
    };
    if (iosHrefs[playerKey]) return iosHrefs[playerKey];

    const noFragment = absolute.replace(/#/g, "%23");
    const desktopHrefs = {
      "desktop-vlc": `vlc://${noFragment}`,
      "desktop-potplayer": `potplayer://${noFragment}`,
      "desktop-iina": `iina://weblink?url=${encoded}`,
      "desktop-mpv": `mpv://${noFragment}`,
      "desktop-kmplayer": `kmplayer://${noFragment}`,
    };
    return desktopHrefs[playerKey] || "";
  };

  const wirePlayerLinks = () => {
    document.querySelectorAll(".player-link[data-player]").forEach((link) => {
      const href = buildPlayerHref(link.dataset.player);
      if (!href) {
        link.setAttribute("aria-disabled", "true");
        link.setAttribute("tabindex", "-1");
        link.removeAttribute("href");
        return;
      }
      link.href = href;
    });

    drawerPanel?.addEventListener("click", (event) => {
      const link = event.target.closest(".player-link[data-player]");
      if (!link || link.getAttribute("aria-disabled") === "true") return;
      // Intents carry their own fallback; desktop schemes cannot be detected.
      if (link.href.startsWith("intent://")) return;
      let leftPage = false;
      const onVisibilityChange = () => {
        if (document.hidden) leftPage = true;
      };
      document.addEventListener("visibilitychange", onVisibilityChange, { once: true });
      setTimeout(() => {
        document.removeEventListener("visibilitychange", onVisibilityChange);
        if (!leftPage) {
          showToast("If the player did not open, copy the link and paste it in the app.");
        }
      }, 1100);
    });
  };

  // ── Drawer toggle ────────────────────────────────────────────────────────
  /**
   * max-height + overflow hide the panel visually but leave its links tabbable;
   * `inert` removes them from keyboard order. The forced reflow lets the first
   * max-height transition run after `inert` clears.
   */
  const setDrawerOpen = (open) => {
    if (!drawerPanel) return;
    if (open) {
      drawerPanel.removeAttribute("inert");
      void drawerPanel.offsetHeight; // flush the inert removal
      drawerPanel.classList.add("open");
    } else {
      drawerPanel.classList.remove("open");
      drawerPanel.setAttribute("inert", "");
    }
    playersToggle?.setAttribute("aria-expanded", String(open));
  };

  playersToggle?.addEventListener("click", () => {
    const expanded = playersToggle.getAttribute("aria-expanded") === "true";
    setDrawerOpen(!expanded);
    if (!expanded) {
      setTimeout(() => {
        drawerPanel?.scrollIntoView({
          behavior: prefersReducedMotion() ? "auto" : "smooth",
          block: "nearest",
        });
      }, 100);
    }
  });

  // ── Copy link ────────────────────────────────────────────────────────────
  let copyResetTimer = null;

  const confirmCopy = () => {
    if (!copyBtn) return;
    clearTimeout(copyResetTimer);
    const label = copyBtn.querySelector("span");
    const originalText = label ? label.textContent : "";
    copyBtn.classList.add("is-confirmed");
    if (label) label.textContent = "Copied";
    copyBtn.setAttribute("aria-label", "Link copied to clipboard");
    copyResetTimer = setTimeout(() => {
      copyBtn.classList.remove("is-confirmed");
      if (label) label.textContent = originalText || "Copy Link";
      copyBtn.setAttribute("aria-label", "Copy Link");
    }, TOAST_VISIBLE_MS);
  };

  copyBtn?.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(absoluteStreamUrl());
      confirmCopy();
    } catch {
      console.warn('Clipboard API unavailable, falling back to execCommand("copy")');
      const scratch = document.createElement("textarea");
      scratch.value = absoluteStreamUrl();
      scratch.style.cssText = "position:fixed;left:-9999px";
      document.body.appendChild(scratch);
      scratch.select();
      try {
        // Returns false without throwing when the browser refuses the copy.
        if (!document.execCommand("copy")) throw new Error("copy rejected");
        confirmCopy();
      } catch {
        showToast("Failed to copy — try selecting the link manually", "error");
      }
      scratch.remove();
    }
  });

  // ── Download feedback ────────────────────────────────────────────────────
  let downloadResetTimer = null;

  downloadBtn?.addEventListener("click", () => {
    clearTimeout(downloadResetTimer);
    const label = downloadBtn.querySelector("span");
    const originalText = label ? label.textContent : "";
    downloadBtn.classList.add("is-confirmed");
    if (label) label.textContent = "Starting…";
    downloadResetTimer = setTimeout(() => {
      downloadBtn.classList.remove("is-confirmed");
      if (label) label.textContent = originalText || "Download";
    }, 1500);
  });

  // ── Footer year ──────────────────────────────────────────────────────────
  const setCopyrightYear = () => {
    const slot = document.getElementById("copyrightYear");
    if (slot) slot.textContent = new Date().getFullYear();
  };

  initTheme();
  prunePlayerGroups();
  wirePlayerLinks();
  setCopyrightYear();
})();
