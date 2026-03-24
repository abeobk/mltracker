import { createApp, ref, computed, watch, onMounted, onUnmounted, nextTick, defineComponent, h } from 'vue';
// Chart.js loaded as a plain <script> tag — access via global
const Chart = window.Chart;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

async function api(path, opts = {}) {
  const r = await fetch(path, { credentials: 'same-origin', ...opts });
  if (!r.ok) {
    const body = await r.json().catch(() => ({}));
    throw new Error(body.error || `HTTP ${r.status}`);
  }
  return r.json();
}

function run_color(idx) {
  return `hsl(${(idx * 137) % 360}, 65%, 55%)`;
}

// ---------------------------------------------------------------------------
// TopBar
// ---------------------------------------------------------------------------
const TopBar = defineComponent({
  props: ['user'],
  emits: ['toggle-theme', 'logout', 'key-copied'],
  setup(props, { emit }) {
    async function copy_key() {
      if (!props.user?.api_key) return;
      await navigator.clipboard.writeText(props.user.api_key).catch(() => {});
      emit('key-copied');
    }
    return () => h('div', { class: 'topbar' }, [
      h('a', { class: 'logo', href: '/' }, [
        h('i', { class: 'fa-solid fa-chart-line' }),
        'WandB Clone',
      ]),
      h('div', { class: 'spacer' }),
      props.user ? [
        props.user.picture
          ? h('img', { class: 'user-avatar', src: props.user.picture, alt: '' })
          : h('i', { class: 'fa-solid fa-circle-user', style: 'font-size:22px;color:var(--text-dim)' }),
        h('span', { class: 'user-name', title: props.user.name }, props.user.name || props.user.email),
        h('button', { title: 'Copy API key', onClick: copy_key }, [h('i', { class: 'fa-solid fa-key' })]),
      ] : null,
      h('button', { title: 'Toggle theme', onClick: () => emit('toggle-theme') }, [
        h('i', { class: 'fa-solid fa-sun' }),
      ]),
      h('button', { title: 'Logout', onClick: () => emit('logout') }, [
        h('i', { class: 'fa-solid fa-right-from-bracket' }),
      ]),
    ]);
  },
});

// ---------------------------------------------------------------------------
// LeftPanel
// ---------------------------------------------------------------------------
const LeftPanel = defineComponent({
  props: ['projects', 'sel_project_id', 'sel_run_id'],
  emits: ['select-project', 'select-run', 'delete-project', 'delete-run'],
  setup(props, { emit }) {
    const collapsed = ref({});   // project_id → bool

    function toggle_collapse(proj_id, e) {
      e.stopPropagation();
      collapsed.value[proj_id] = !collapsed.value[proj_id];
    }

    function confirm_delete_project(proj, e) {
      e.stopPropagation();
      if (confirm(`Delete project "${proj.name}" and all its runs?`)) {
        emit('delete-project', proj.id);
      }
    }

    function confirm_delete_run(run, e) {
      e.stopPropagation();
      if (confirm(`Delete run "${run.name}"?`)) {
        emit('delete-run', run.id);
      }
    }

    return () => h('div', { class: 'left-panel' }, [
      h('div', { class: 'left-panel-header' }, [
        h('span', 'Projects'),
        h('i', { class: 'fa-solid fa-layer-group', style: 'opacity:0.4' }),
      ]),
      props.projects.length === 0
        ? h('div', { style: 'padding:16px 10px;color:var(--text-dim);font-size:12px' },
            'No projects yet.')
        : props.projects.map(proj =>
            h('div', { key: proj.id }, [
              // Project row
              h('div', {
                class: ['tree-item', 'project-row',
                        props.sel_project_id === proj.id && !props.sel_run_id ? 'selected' : ''],
                onClick: () => emit('select-project', proj),
                title: proj.name,
              }, [
                h('i', {
                  class: ['collapse-icon', 'fa-solid',
                          collapsed.value[proj.id] ? 'fa-chevron-right' : 'fa-chevron-down'],
                  onClick: e => toggle_collapse(proj.id, e),
                }),
                h('i', { class: 'fa-solid fa-folder folder-icon' }),
                h('span', { class: 'item-name' }, proj.name),
                h('button', {
                  class: 'icon-btn',
                  title: 'Delete project',
                  onClick: e => confirm_delete_project(proj, e),
                }, h('i', { class: 'fa-solid fa-trash' })),
              ]),
              // Run rows
              !collapsed.value[proj.id] && (proj.runs || []).map(run =>
                h('div', {
                  key: run.id,
                  class: ['tree-item', 'run-row', props.sel_run_id === run.id ? 'selected' : ''],
                  onClick: () => emit('select-run', proj, run),
                  title: run.name,
                }, [
                  h('span', { class: ['status-dot', run.status] }),
                  h('span', { class: 'item-name' }, run.name),
                  h('button', {
                    class: 'icon-btn',
                    title: 'Delete run',
                    onClick: e => confirm_delete_run(run, e),
                  }, h('i', { class: 'fa-solid fa-trash' })),
                ])
              ),
            ])
          ),
    ]);
  },
});

// ---------------------------------------------------------------------------
// MetricChart
// ---------------------------------------------------------------------------
const MetricChart = defineComponent({
  props: ['metric_key', 'datasets', 'is_live', 'downsampled'],
  setup(props) {
    let _chart = null;
    const canvas_ref = ref(null);

    function build_chart() {
      if (!canvas_ref.value) return;
      if (_chart) { _chart.destroy(); _chart = null; }

      const style = getComputedStyle(document.documentElement);
      const grid_color  = style.getPropertyValue('--border').trim();
      const label_color = style.getPropertyValue('--text-dim').trim();

      _chart = new Chart(canvas_ref.value, {
        type: 'line',
        data: {
          datasets: props.datasets.map((ds, idx) => ({
            label: ds.label,
            data: ds.points.map(p => ({ x: p.step, y: p.value })),
            borderColor: run_color(idx),
            backgroundColor: 'transparent',
            borderWidth: 1.5,
            pointRadius: props.datasets[0].points.length > 200 ? 0 : 2,
            tension: 0.1,
          })),
        },
        options: {
          animation: props.is_live ? false : { duration: 300 },
          responsive: true,
          maintainAspectRatio: false,
          parsing: false,
          scales: {
            x: {
              type: 'linear',
              title: { display: true, text: 'step', color: label_color },
              grid: { color: grid_color },
              ticks: { color: label_color },
            },
            y: {
              grid: { color: grid_color },
              ticks: { color: label_color },
            },
          },
          plugins: {
            legend: {
              display: props.datasets.length > 1,
              labels: { color: label_color, boxWidth: 12, font: { size: 11 } },
            },
          },
        },
      });
    }

    watch(() => [props.datasets, props.metric_key], () => {
      nextTick(build_chart);
    }, { deep: true });

    onMounted(() => nextTick(build_chart));
    onUnmounted(() => { if (_chart) { _chart.destroy(); _chart = null; } });

    return () => h('div', { class: 'chart-card' }, [
      h('div', { class: 'card-title' }, [
        props.metric_key,
        props.downsampled
          ? h('span', { class: 'downsampled-badge' }, 'downsampled')
          : null,
      ]),
      h('div', { class: 'chart-canvas-wrap' }, [
        h('canvas', { ref: canvas_ref }),
      ]),
    ]);
  },
});

// ---------------------------------------------------------------------------
// ImageSlider
// ---------------------------------------------------------------------------
const ImageSlider = defineComponent({
  props: ['img_key', 'images'],   // images = [{step, url}]
  setup(props) {
    const _idx = ref(0);
    const current = computed(() => props.images[_idx.value] || null);

    watch(() => props.images, () => { _idx.value = 0; });

    return () => h('div', { class: 'img-slider' }, [
      h('div', { class: 'slider-title' }, props.img_key),
      h('div', { class: 'slider-controls' }, [
        h('button', {
          disabled: _idx.value === 0,
          onClick: () => { if (_idx.value > 0) _idx.value--; },
        }, h('i', { class: 'fa-solid fa-backward-step' })),
        h('span', { class: 'step-label' },
          current.value ? `step ${current.value.step}` : '—'),
        h('button', {
          disabled: _idx.value >= props.images.length - 1,
          onClick: () => { if (_idx.value < props.images.length - 1) _idx.value++; },
        }, h('i', { class: 'fa-solid fa-forward-step' })),
        h('span', { class: 'step-label', style: 'margin-left:4px;opacity:0.6' },
          `${_idx.value + 1} / ${props.images.length}`),
      ]),
      current.value
        ? h('img', { src: current.value.url, alt: `${props.img_key} step ${current.value.step}` })
        : h('div', { style: 'color:var(--text-dim);font-size:12px' }, 'No images'),
    ]);
  },
});

// ---------------------------------------------------------------------------
// MainPanel
// ---------------------------------------------------------------------------
const MainPanel = defineComponent({
  props: ['dash', 'is_loading', 'sel_project', 'sel_run'],
  setup(props) {
    return () => {
      if (props.is_loading) {
        return h('div', { class: 'main-panel' }, [
          h('div', { class: 'loading-row' }, [
            h('i', { class: 'fa-solid fa-spinner fa-spin' }),
            'Loading…',
          ]),
        ]);
      }

      if (!props.sel_project && !props.sel_run) {
        return h('div', { class: 'main-panel' }, [
          h('div', { class: 'empty-state' }, [
            h('i', { class: 'fa-solid fa-chart-line empty-icon' }),
            h('span', { class: 'empty-text' }, 'Select a project or run to view charts'),
          ]),
        ]);
      }

      const { metrics, image_keys, images, downsampled } = props.dash;
      const is_live = props.sel_run?.status === 'running';
      const children = [];

      // Metric charts
      const metric_keys = Object.keys(metrics || {});
      if (metric_keys.length) {
        children.push(h('div', { class: 'section-heading' }, 'Metrics'));
        for (const key of metric_keys) {
          const data = metrics[key];   // [{run_label, points:[{step,value}]}]
          children.push(h(MetricChart, {
            key,
            metric_key: key,
            datasets: data,
            is_live,
            downsampled: downsampled,
          }));
        }
      }

      // Image sliders (run view only)
      if (props.sel_run && (image_keys || []).length) {
        children.push(h('div', { class: 'section-heading', style: 'margin-top:8px' }, 'Images'));
        for (const key of image_keys) {
          children.push(h(ImageSlider, {
            key,
            img_key: key,
            images: (images || {})[key] || [],
          }));
        }
      }

      if (!children.length) {
        return h('div', { class: 'main-panel' }, [
          h('div', { class: 'empty-state' }, [
            h('i', { class: 'fa-solid fa-inbox empty-icon' }),
            h('span', { class: 'empty-text' }, 'No data logged yet'),
          ]),
        ]);
      }

      return h('div', { class: 'main-panel' }, children);
    };
  },
});

// ---------------------------------------------------------------------------
// StatusBar
// ---------------------------------------------------------------------------
const StatusBar = defineComponent({
  props: ['sel_project', 'sel_run'],
  setup(props) {
    return () => {
      const segs = [];
      if (props.sel_project) {
        segs.push(h('span', { class: 'status-segment' }, [
          h('i', { class: 'fa-solid fa-folder' }),
          h('span', { class: 'status-value' }, props.sel_project.name),
        ]));
      }
      if (props.sel_run) {
        segs.push(h('span', { class: 'status-segment' }, [
          h('i', { class: 'fa-solid fa-person-running' }),
          h('span', { class: 'status-value' }, props.sel_run.name),
          h('span', { class: ['status-badge', props.sel_run.status] }, props.sel_run.status),
        ]));
      }
      const right = props.sel_project && !props.sel_run
        ? `${(props.sel_project.runs || []).length} run${(props.sel_project.runs || []).length !== 1 ? 's' : ''}`
        : null;

      return h('div', { class: 'statusbar' }, [
        ...segs,
        h('div', { class: 'spacer' }),
        right ? h('span', right) : null,
      ]);
    };
  },
});

// ---------------------------------------------------------------------------
// Root App
// ---------------------------------------------------------------------------
const App = defineComponent({
  setup() {
    const user          = ref(null);
    const projects      = ref([]);
    const sel_project   = ref(null);
    const sel_run       = ref(null);
    const is_loading    = ref(false);
    const dash          = ref({ metrics: {}, image_keys: [], images: {}, downsampled: false });

    // Auto-refresh state (setTimeout-based with backoff)
    let _refresh_timer  = null;
    let _refresh_delay  = 5000;
    let _fail_count     = 0;
    let _refresh_run_id = null;

    // -----------------------------------------------------------------------
    // Auth / init
    // -----------------------------------------------------------------------
    async function init() {
      const me = await api('/auth/me');
      if (!me.logged_in) { window.location = '/auth/login'; return; }
      user.value = me;
      await load_projects();
    }

    async function load_projects() {
      const list = await api('/api/v1/projects');
      // Load runs for each project in parallel
      await Promise.all(list.map(async proj => {
        proj.runs = await api(`/api/v1/projects/${proj.id}/runs`).catch(() => []);
      }));
      projects.value = list;
    }

    // -----------------------------------------------------------------------
    // Selection
    // -----------------------------------------------------------------------
    let _select_timer = null;
    function select_project(proj) {
      clearTimeout(_select_timer);
      _select_timer = setTimeout(() => _do_select_project(proj), 150);
    }

    function select_run(proj, run) {
      clearTimeout(_select_timer);
      _select_timer = setTimeout(() => _do_select_run(proj, run), 150);
    }

    async function _do_select_project(proj) {
      stop_refresh();
      sel_run.value     = null;
      sel_project.value = proj;
      is_loading.value  = true;
      try {
        await load_project_dash(proj);
      } finally {
        is_loading.value = false;
      }
    }

    async function _do_select_run(proj, run) {
      stop_refresh();
      sel_project.value = proj;
      sel_run.value     = run;
      is_loading.value  = true;
      try {
        await load_run_dash(run);
      } finally {
        is_loading.value = false;
      }
      if (run.status === 'running') start_refresh(run.id);
    }

    // -----------------------------------------------------------------------
    // Dashboard loaders
    // -----------------------------------------------------------------------
    async function load_project_dash(proj) {
      const runs = proj.runs || [];
      if (!runs.length) { dash.value = { metrics: {}, image_keys: [], images: {}, downsampled: false }; return; }

      // Collect union of all metric keys across runs
      const key_sets = await Promise.all(
        runs.map(r => api(`/api/v1/runs/${r.id}/metric-keys`).catch(() => []))
      );
      const all_keys = [...new Set(key_sets.flat())];

      if (!all_keys.length) { dash.value = { metrics: {}, image_keys: [], images: {}, downsampled: false }; return; }

      // Fetch metrics for each run (only the union of keys)
      const metrics_by_run = await Promise.all(
        runs.map(r => api(`/api/v1/runs/${r.id}/metrics?keys=${all_keys.join(',')}`).catch(() => ({ metrics: {}, downsampled: false })))
      );

      // Build {key: [{label, points}]} — one dataset per run
      const grouped = {};
      let any_downsampled = false;
      for (const key of all_keys) {
        grouped[key] = [];
        metrics_by_run.forEach((resp, idx) => {
          if (resp.downsampled) any_downsampled = true;
          const pts = (resp.metrics || {})[key] || [];
          if (pts.length) grouped[key].push({ label: runs[idx].name, points: pts });
        });
        if (!grouped[key].length) delete grouped[key];
      }

      dash.value = { metrics: grouped, image_keys: [], images: {}, downsampled: any_downsampled };
    }

    async function load_run_dash(run) {
      const [keys_resp, img_keys_resp] = await Promise.all([
        api(`/api/v1/runs/${run.id}/metric-keys`).catch(() => []),
        api(`/api/v1/runs/${run.id}/image-keys`).catch(() => []),
      ]);

      let metrics_resp = { metrics: {}, downsampled: false };
      if (keys_resp.length) {
        metrics_resp = await api(
          `/api/v1/runs/${run.id}/metrics?keys=${keys_resp.join(',')}`
        ).catch(() => ({ metrics: {}, downsampled: false }));
      }

      // Wrap each key's data as a single-dataset array for MetricChart
      const grouped = {};
      for (const [key, pts] of Object.entries(metrics_resp.metrics || {})) {
        grouped[key] = [{ label: run.name, points: pts }];
      }

      // Fetch images for each image key
      const images = {};
      await Promise.all(
        img_keys_resp.map(async key => {
          const resp = await api(`/api/v1/runs/${run.id}/images?key=${encodeURIComponent(key)}`).catch(() => ({ images: [] }));
          images[key] = resp.images || [];
        })
      );

      dash.value = {
        metrics: grouped,
        image_keys: img_keys_resp,
        images,
        downsampled: metrics_resp.downsampled,
      };
    }

    // -----------------------------------------------------------------------
    // Auto-refresh (setTimeout + backoff)
    // -----------------------------------------------------------------------
    function start_refresh(run_id) {
      _refresh_run_id = run_id;
      _refresh_delay  = 5000;
      _fail_count     = 0;
      schedule_refresh();
    }

    function schedule_refresh() {
      _refresh_timer = setTimeout(do_refresh, _refresh_delay);
    }

    async function do_refresh() {
      if (!_refresh_run_id || sel_run.value?.id !== _refresh_run_id) return;
      try {
        // Re-fetch run status first
        const run_data = await api(`/api/v1/runs/${_refresh_run_id}`);
        // Update run in tree
        const proj = projects.value.find(p => p.id === sel_project.value?.id);
        if (proj) {
          const idx = proj.runs.findIndex(r => r.id === _refresh_run_id);
          if (idx !== -1) proj.runs[idx] = { ...proj.runs[idx], ...run_data };
        }
        sel_run.value = { ...sel_run.value, ...run_data };

        await load_run_dash(sel_run.value);

        _fail_count    = 0;
        _refresh_delay = 5000;

        if (sel_run.value?.status === 'running') schedule_refresh();
        else stop_refresh();   // run finished while watching
      } catch (_) {
        _fail_count++;
        _refresh_delay = Math.min(60000, 5000 * Math.pow(2, _fail_count));
        schedule_refresh();
      }
    }

    function stop_refresh() {
      clearTimeout(_refresh_timer);
      _refresh_timer  = null;
      _refresh_run_id = null;
    }

    onUnmounted(stop_refresh);

    // -----------------------------------------------------------------------
    // Delete handlers
    // -----------------------------------------------------------------------
    async function delete_project(proj_id) {
      await api(`/api/v1/projects/${proj_id}`, { method: 'DELETE' });
      if (sel_project.value?.id === proj_id) {
        stop_refresh();
        sel_project.value = null;
        sel_run.value     = null;
        dash.value = { metrics: {}, image_keys: [], images: {}, downsampled: false };
      }
      await load_projects();
    }

    async function delete_run(run_id) {
      await api(`/api/v1/runs/${run_id}`, { method: 'DELETE' });
      if (sel_run.value?.id === run_id) {
        stop_refresh();
        sel_run.value = null;
        if (sel_project.value) await _do_select_project(sel_project.value);
      }
      // Refresh runs list for the containing project
      await load_projects();
    }

    // -----------------------------------------------------------------------
    // Theme toggle
    // -----------------------------------------------------------------------
    function toggle_theme() {
      document.body.classList.toggle('light');
    }

    // -----------------------------------------------------------------------
    // Drag-resize left panel
    // -----------------------------------------------------------------------
    function start_resize(e) {
      const start_x = e.clientX;
      const start_w = parseInt(
        getComputedStyle(document.documentElement).getPropertyValue('--left-w')
      );
      const move = ev => {
        const w = Math.max(160, Math.min(480, start_w + ev.clientX - start_x));
        document.documentElement.style.setProperty('--left-w', `${w}px`);
      };
      const up = () => {
        window.removeEventListener('mousemove', move);
        window.removeEventListener('mouseup', up);
      };
      window.addEventListener('mousemove', move);
      window.addEventListener('mouseup', up);
    }

    onMounted(init);

    // -----------------------------------------------------------------------
    // Render
    // -----------------------------------------------------------------------
    return () => h('div', { id: 'app-inner', style: 'display:contents' }, [
      h(TopBar, {
        user: user.value,
        onToggleTheme: toggle_theme,
        onLogout: () => { window.location = '/auth/logout'; },
        onKeyCopied: () => { /* could show a toast */ },
      }),
      h(LeftPanel, {
        projects: projects.value,
        sel_project_id: sel_project.value?.id ?? null,
        sel_run_id: sel_run.value?.id ?? null,
        onSelectProject: select_project,
        onSelectRun: select_run,
        onDeleteProject: delete_project,
        onDeleteRun: delete_run,
      }),
      h('div', {
        class: 'resize-handle lhandle',
        onMousedown: start_resize,
      }),
      h(MainPanel, {
        dash: dash.value,
        is_loading: is_loading.value,
        sel_project: sel_project.value,
        sel_run: sel_run.value,
      }),
      h(StatusBar, {
        sel_project: sel_project.value,
        sel_run: sel_run.value,
      }),
    ]);
  },
});

const app = createApp(App);
app.mount('#app');
window.__app = { reload: () => app._instance?.proxy?.$forceUpdate?.() };
