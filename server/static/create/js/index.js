(function () {
  const CREATE_PARTIALS = [
  "hub",
  "database",
  "ubuntu",
  "web"
  ];

  async function loadCreateViews() {
    const mount = document.getElementById("create-views-mount");
    if (!mount) return;

    const fragments = await Promise.all(
      CREATE_PARTIALS.map(name =>
        fetch(`/static/create/html/${name}.html`).then(res => {
          if (!res.ok) throw new Error(`Failed to load create view: ${name}`);
          return res.text();
        })
      )
    );

    mount.innerHTML = fragments.join("");
  }

  window.ZenoCreate = {
    deps: null,

    async init(deps) {
      this.deps = deps;
      await loadCreateViews();
      if (typeof this.hub?.init === "function") this.hub.init(deps);
      if (typeof this.database?.init === "function") this.database.init(deps);
      if (typeof this.ubuntu?.init === "function") this.ubuntu.init(deps);
      if (typeof this.web?.init === "function") this.web.init(deps);
    },

    hub: {},
    database: {},
    ubuntu: {},
    web: {}
  };
})();
