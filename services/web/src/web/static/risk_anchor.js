// UX-6: /risk was one page; its sections are now sub-pages. A link to an old in-page section
// (a bookmark of /risk#risk-ranking) lands here, on ex-post, since a fragment never reaches the
// server. This sends it on to the sub-page that holds the section, with its query and anchor.
// replace(), not assign(), so Back doesn't return here and bounce forward again.
(() => {
  const subPages = {
    "risk-ex-ante": "/risk/ex-ante",
    "risk-ex-ante-rolling": "/risk/ex-ante",
    "risk-rolling-correlation": "/risk/ex-ante",
    "risk-ex-ante-regimes": "/risk/ex-ante",
    "risk-what-if": "/risk/what-if",
    "risk-ranking": "/risk/what-if",
    "risk-concentration": "/risk/concentration",
  };
  const path = subPages[window.location.hash.slice(1)];
  if (path) {
    window.location.replace(path + window.location.search + window.location.hash);
  }
})();
