import { createApp, ref, watch, onMounted, onUnmounted, nextTick, defineComponent, h } from 'vue';
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

// Group flat card_order keys by path prefix (e.g. "train/loss" → group "train")
// Returns [{type:'single', unit_key, key} | {type:'group', unit_key, prefix, keys:[]}]
function compute_units(order) {
  const units = [];
  const group_idx = {}; // prefix → index in units
  for (const key of order) {
    const slash = key.indexOf('/');
    if (slash === -1) {
      units.push({ type: 'single', unit_key: key, key });
    } else {
      const prefix = key.slice(0, slash);
      const gk = 'group::' + prefix;
      if (gk in group_idx) {
        units[group_idx[gk]].keys.push(key);
      } else {
        group_idx[gk] = units.length;
        units.push({ type: 'group', unit_key: gk, prefix, keys: [key] });
      }
    }
  }
  return units;
}

// ---------------------------------------------------------------------------
// TopBar
// ---------------------------------------------------------------------------
const TopBar = defineComponent({
  props: ['user', 'admin_active'],
  emits: ['toggle-theme', 'logout', 'key-copied', 'toggle-admin', 'toggle-panel'],
  setup(props, { emit }) {
    async function copy_key() {
      if (!props.user?.api_key) return;
      await navigator.clipboard.writeText(props.user.api_key).catch(() => {});
      emit('key-copied');
    }
    return () => h('div', { class: 'topbar' }, [
      // Hamburger — only visible on mobile via CSS
      h('button', { class: 'menu-btn', title: 'Toggle panel', onClick: () => emit('toggle-panel') }, [
        h('i', { class: 'fa-solid fa-bars' }),
      ]),
      h('a', { class: 'logo', href: '/' }, [
        h('i', { class: 'fa-solid fa-chart-line' }),
        'MLTracker',
      ]),
      h('div', { class: 'spacer' }),
      props.user ? [
        props.user.picture
          ? h('img', { class: 'user-avatar', src: props.user.picture, alt: '' })
          : h('i', { class: 'fa-solid fa-circle-user', style: 'font-size:22px;color:var(--text-dim)' }),
        h('span', { class: 'user-name', title: props.user.name }, props.user.name || props.user.email),
        h('button', { title: 'Copy API key', onClick: copy_key }, [h('i', { class: 'fa-solid fa-key' })]),
      ] : null,
      props.user?.is_admin
        ? h('button', {
            title: 'Admin dashboard',
            class: props.admin_active ? 'active-btn' : '',
            onClick: () => emit('toggle-admin'),
          }, [h('i', { class: 'fa-solid fa-users-gear' })])
        : null,
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
// AdminPanel
// ---------------------------------------------------------------------------
const AdminPanel = defineComponent({
  setup() {
    const users    = ref([]);
    const loading  = ref(true);
    const error    = ref(null);

    function fmt_duration(seconds) {
      if (!seconds) return '—';
      const h = Math.floor(seconds / 3600);
      const m = Math.floor((seconds % 3600) / 60);
      if (h > 0) return `${h}h ${m}m`;
      return `${m}m`;
    }

    function fmt_date(ts) {
      if (!ts) return '—';
      return new Date(ts * 1000).toLocaleDateString();
    }

    onMounted(async () => {
      try {
        users.value = await api('/api/v1/admin/users');
      } catch (e) {
        error.value = e.message;
      } finally {
        loading.value = false;
      }
    });

    return () => {
      if (loading.value) return h('div', { class: 'main-panel' }, [
        h('div', { class: 'loading-row' }, [h('i', { class: 'fa-solid fa-spinner fa-spin' }), 'Loading…']),
      ]);
      if (error.value) return h('div', { class: 'main-panel' }, [
        h('div', { class: 'empty-state' }, [h('span', error.value)]),
      ]);

      return h('div', { class: 'main-panel' }, [
        h('div', { class: 'admin-panel' }, [
          h('div', { class: 'admin-header' }, [
            h('i', { class: 'fa-solid fa-users-gear' }),
            h('span', `Users  (${users.value.length})`),
          ]),
          h('table', { class: 'admin-table' }, [
            h('thead', [
              h('tr', [
                h('th', '#'),
                h('th', 'User'),
                h('th', 'Projects'),
                h('th', 'Runs'),
                h('th', 'Tracking time'),
                h('th', 'Last active'),
                h('th', 'Joined'),
              ]),
            ]),
            h('tbody', users.value.map((u, idx) =>
              h('tr', { key: u.id, class: idx === 0 ? 'admin-row' : '' }, [
                h('td', { class: 'admin-cell-dim' }, u.id),
                h('td', [
                  h('div', { class: 'admin-user-cell' }, [
                    u.picture
                      ? h('img', { class: 'admin-avatar', src: u.picture, alt: '' })
                      : h('i', { class: 'fa-solid fa-circle-user admin-avatar-icon' }),
                    h('div', [
                      h('div', u.name || '—'),
                      h('div', { class: 'admin-cell-dim' }, u.email),
                    ]),
                  ]),
                ]),
                h('td', u.project_count),
                h('td', u.run_count),
                h('td', fmt_duration(u.total_run_seconds)),
                h('td', fmt_date(u.last_active)),
                h('td', fmt_date(u.created_at)),
              ])
            )),
          ]),
        ]),
      ]);
    };
  },
});

// ---------------------------------------------------------------------------
// LeftPanel
// ---------------------------------------------------------------------------
const LeftPanel = defineComponent({
  props: ['projects', 'sel_project_id', 'sel_run_id'],
  emits: ['select-project', 'select-run', 'delete-project', 'delete-run'],
  setup(props, { emit }) {
    const collapsed = ref({});

    function toggle_collapse(proj_id, e) {
      e.stopPropagation();
      collapsed.value[proj_id] = !collapsed.value[proj_id];
    }
    function confirm_delete_project(proj, e) {
      e.stopPropagation();
      if (confirm(`Delete project "${proj.name}" and all its runs?`)) emit('delete-project', proj.id);
    }
    function confirm_delete_run(run, e) {
      e.stopPropagation();
      if (confirm(`Delete run "${run.name}"?`)) emit('delete-run', run.id);
    }

    return () => h('div', { class: 'left-panel' }, [
      h('div', { class: 'left-panel-header' }, [
        h('span', 'Projects'),
        h('i', { class: 'fa-solid fa-layer-group', style: 'opacity:0.4' }),
      ]),
      props.projects.length === 0
        ? h('div', { style: 'padding:16px 10px;color:var(--text-dim);font-size:12px' }, 'No projects yet.')
        : props.projects.map(proj =>
            h('div', { key: proj.id }, [
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
                  class: 'icon-btn', title: 'Delete project',
                  onClick: e => confirm_delete_project(proj, e),
                }, h('i', { class: 'fa-solid fa-trash' })),
              ]),
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
                    class: 'icon-btn', title: 'Delete run',
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
// MetricChart  (rendered inside DashCard — no own outer wrapper)
// ---------------------------------------------------------------------------
const MetricChart = defineComponent({
  props: ['metric_key', 'datasets', 'is_live'],
  setup(props) {
    let _chart = null;
    let _ro    = null;
    const canvas_ref = ref(null);

    function build_chart() {
      if (!canvas_ref.value) return;
      if (_chart) { _chart.destroy(); _chart = null; }
      const style       = getComputedStyle(document.documentElement);
      const grid_color  = style.getPropertyValue('--border').trim();
      const label_color = style.getPropertyValue('--text-dim').trim();
      _chart = new Chart(canvas_ref.value, {
        type: 'line',
        data: {
          datasets: props.datasets.map((ds, idx) => ({
            label: ds.label,
            data: ds.points.map(p => ({ x: p.step, y: p.value })),
            borderColor: run_color(idx),
            backgroundColor: run_color(idx),
            borderWidth: 1.5,
            pointRadius: (props.datasets[0]?.points.length ?? 0) > 200 ? 0 : 2,
            tension: 0.1,
          })),
        },
        options: {
          animation: false,
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
              labels: { color: label_color, usePointStyle: true, pointStyle: 'circle', boxWidth: 6, boxHeight: 6, font: { size: 11 } },
            },
          },
        },
      });
    }

    watch(() => [props.datasets, props.metric_key], () => { nextTick(build_chart); }, { deep: true });

    onMounted(() => {
      nextTick(build_chart);
      if (canvas_ref.value) {
        _ro = new ResizeObserver(() => { if (_chart) _chart.resize(); });
        _ro.observe(canvas_ref.value.parentElement);
      }
    });
    onUnmounted(() => {
      _ro?.disconnect();
      if (_chart) { _chart.destroy(); _chart = null; }
    });

    return () => h('div', { class: 'chart-canvas-wrap' }, [
      h('canvas', { ref: canvas_ref }),
    ]);
  },
});

// ---------------------------------------------------------------------------
// ImageSlider  (rendered inside DashCard — no own outer wrapper)
// props.runs: [{label, images: [{step, url}]}]
// One card shows all runs at the same step — mirrors multi-run metric charts.
// ---------------------------------------------------------------------------
const ImageSlider = defineComponent({
  props: ['img_key', 'runs'],
  setup(props) {
    const _idx = ref(0);
    // Reset when the key changes (new selection)
    watch(() => props.img_key, () => { _idx.value = 0; });
    // Jump to latest when new images arrive
    watch(
      () => (props.runs || []).flatMap(r => r.images).length,
      (newLen, oldLen) => {
        if (newLen > oldLen) {
          const steps = [...new Set(
            (props.runs || []).flatMap(r => r.images.map(img => img.step))
          )];
          _idx.value = steps.length - 1;
        }
      }
    );

    return () => {
      // Sorted union of all steps across every run
      const all_steps = [...new Set(
        (props.runs || []).flatMap(r => r.images.map(img => img.step))
      )].sort((a, b) => a - b);

      const total   = all_steps.length;
      const clamped = Math.max(0, Math.min(_idx.value, total - 1));
      const step    = all_steps[clamped];
      const is_multi = (props.runs || []).length > 1;

      const run_cells = (props.runs || []).map(r => {
        const img = step !== undefined ? r.images.find(x => x.step === step) : null;
        return h('div', { class: 'img-run-cell', key: r.label }, [
          is_multi
            ? h('div', { class: 'run-img-label', title: r.label }, r.label)
            : null,
          img
            ? h('img', { src: img.url, alt: `${r.label} step ${step}` })
            : h('div', { class: 'no-images' }, [
                h('i', { class: 'fa-solid fa-image' }),
                r.images.length ? ' No image at this step' : ' No images',
              ]),
        ]);
      });

      return h('div', { class: 'img-slider-body' }, [
        h('div', { class: 'slider-controls' }, [
          h('button', {
            disabled: clamped === 0,
            onClick: () => { if (_idx.value > 0) _idx.value--; },
          }, h('i', { class: 'fa-solid fa-backward-step' })),
          h('span', { class: 'step-label' }, step !== undefined ? `step ${step}` : '—'),
          h('button', {
            disabled: clamped >= total - 1,
            onClick: () => { if (_idx.value < total - 1) _idx.value++; },
          }, h('i', { class: 'fa-solid fa-forward-step' })),
          total > 0
            ? h('span', { class: 'step-label', style: 'margin-left:4px;opacity:0.6' }, `${clamped + 1} / ${total}`)
            : null,
        ]),
        h('div', { class: is_multi ? 'img-grid-multi' : 'img-grid-single' }, run_cells),
      ]);
    };
  },
});

// ---------------------------------------------------------------------------
// DashCard  — draggable + resizable wrapper
// ---------------------------------------------------------------------------
const DashCard = defineComponent({
  props: ['card_label', 'is_metric', 'is_dragging', 'is_drag_over', 'width', 'height', 'downsampled', 'collapsed'],
  emits: ['drag-start', 'drag-enter', 'resize', 'toggle-collapse'],
  setup(props, { emit, slots }) {
    function on_resize_mousedown(e) {
      if (e.button !== 0) return;
      e.preventDefault();
      e.stopPropagation();
      const start_x = e.clientX, start_y = e.clientY;
      const start_w = props.width,  start_h = props.height;
      document.body.style.userSelect = 'none';
      const on_move = ev => {
        emit('resize', {
          w: Math.max(280, start_w + ev.clientX - start_x),
          h: Math.max(150, start_h + ev.clientY - start_y),
        });
      };
      const on_up = () => {
        document.body.style.userSelect = '';
        window.removeEventListener('mousemove', on_move);
        window.removeEventListener('mouseup', on_up);
      };
      window.addEventListener('mousemove', on_move);
      window.addEventListener('mouseup', on_up);
    }

    return () => h('div', {
      class: [
        'dashboard-card',
        props.is_dragging  ? 'is-dragging'  : '',
        props.is_drag_over && !props.is_dragging ? 'is-drag-over' : '',
        props.collapsed ? 'is-collapsed' : '',
      ],
      style: props.width != null ? { width: props.width + 'px' } : {},
      onMouseenter: () => emit('drag-enter'),
    }, [
      h('div', {
        class: 'card-drag-bar',
        onMousedown: e => { if (e.button === 0) emit('drag-start'); },
      }, [
        h('i', { class: 'fa-solid fa-grip-vertical card-grip' }),
        h('span', { class: 'card-key-label', title: props.card_label }, props.card_label),
        props.is_metric && props.downsampled
          ? h('span', { class: 'downsampled-badge' }, 'downsampled') : null,
        !props.is_metric
          ? h('i', { class: 'fa-solid fa-image card-type-icon', title: 'Image' }) : null,
        h('button', {
          class: 'card-collapse-btn',
          title: props.collapsed ? 'Expand' : 'Collapse',
          onMousedown: e => e.stopPropagation(),
          onClick: () => emit('toggle-collapse'),
        }, h('i', { class: `fa-solid fa-chevron-${props.collapsed ? 'down' : 'up'}` })),
      ]),
      !props.collapsed
        ? h('div', { class: 'card-body', style: { height: props.height + 'px' } }, slots.default?.())
        : null,
      !props.collapsed
        ? h('div', { class: 'card-resize-handle', onMousedown: on_resize_mousedown })
        : null,
    ]);
  },
});

// ---------------------------------------------------------------------------
// MetricGroup  — resizable container for path-grouped metric/image cards
// ---------------------------------------------------------------------------
const MetricGroup = defineComponent({
  props: ['prefix', 'width', 'height', 'is_dragging', 'is_drag_over', 'collapsed'],
  emits: ['drag-start', 'drag-enter', 'resize-group', 'toggle-collapse'],
  setup(props, { emit, slots }) {
    function on_resize_mousedown(e) {
      if (e.button !== 0) return;
      e.preventDefault();
      e.stopPropagation();
      const start_x = e.clientX, start_y = e.clientY;
      const start_w = props.width,  start_h = props.height;
      document.body.style.userSelect = 'none';
      const on_move = ev => {
        emit('resize-group', {
          w: Math.max(480, start_w + ev.clientX - start_x),
          h: Math.max(150, start_h + ev.clientY - start_y),
        });
      };
      const on_up = () => {
        document.body.style.userSelect = '';
        window.removeEventListener('mousemove', on_move);
        window.removeEventListener('mouseup', on_up);
      };
      window.addEventListener('mousemove', on_move);
      window.addEventListener('mouseup', on_up);
    }

    return () => h('div', {
      class: [
        'metric-group',
        props.is_dragging ? 'is-dragging' : '',
        props.is_drag_over && !props.is_dragging ? 'is-drag-over' : '',
      ],
      style: { width: props.width + 'px' },
      onMouseenter: () => emit('drag-enter'),
    }, [
      h('div', {
        class: 'metric-group-header',
        onMousedown: e => { if (e.button === 0) emit('drag-start'); },
      }, [
        h('i', { class: 'fa-solid fa-grip-vertical card-grip' }),
        h('i', { class: 'fa-solid fa-folder-open', style: 'font-size:11px;color:var(--text-dim)' }),
        h('span', { class: 'card-key-label' }, props.prefix),
        h('button', {
          class: 'card-collapse-btn',
          title: props.collapsed ? 'Expand' : 'Collapse',
          onMousedown: e => e.stopPropagation(),
          onClick: () => emit('toggle-collapse'),
        }, h('i', { class: `fa-solid fa-chevron-${props.collapsed ? 'down' : 'up'}` })),
      ]),
      !props.collapsed
        ? h('div', { class: 'metric-group-body' }, slots.default?.())
        : null,
      !props.collapsed
        ? h('div', { class: 'card-resize-handle', onMousedown: on_resize_mousedown })
        : null,
    ]);
  },
});

// ---------------------------------------------------------------------------
// MainPanel  — card grid with persistent layout (localStorage)
// ---------------------------------------------------------------------------
const DEFAULT_CHART_H = 220;
const DEFAULT_IMAGE_H = 280;
const DEFAULT_W       = 420;

const MainPanel = defineComponent({
  props: ['dash', 'is_loading', 'sel_project', 'sel_run'],
  setup(props) {
    const card_order    = ref([]);
    const card_sizes    = ref({});
    const dragging_key        = ref(null);
    const drag_over_key       = ref(null);
    const dragging_child_key  = ref(null);
    const drag_over_child_key = ref(null);

    // ── Layout persistence ──────────────────────────────────────────
    function lstore_key() {
      if (props.sel_run?.id)     return `wandb_layout_run_${props.sel_run.id}`;
      if (props.sel_project?.id) return `wandb_layout_proj_${props.sel_project.id}`;
      return null;
    }

    function save_layout() {
      const k = lstore_key();
      if (!k || !card_order.value.length) return;   // skip empty transitional state
      localStorage.setItem(k, JSON.stringify({ order: card_order.value, sizes: card_sizes.value }));
    }

    function load_layout(available_keys) {
      const k = lstore_key();
      if (!k) return null;
      try {
        const saved = JSON.parse(localStorage.getItem(k));
        if (!saved?.order) return null;
        // Restore saved order; add any new keys at the end
        const valid_order = saved.order.filter(key => available_keys.includes(key));
        for (const key of available_keys) {
          if (!valid_order.includes(key)) valid_order.push(key);
        }
        return { order: valid_order, sizes: saved.sizes || {} };
      } catch { return null; }
    }

    function default_size(key) {
      if (key.startsWith('group::')) return { w: DEFAULT_W * 2 + 12, h: DEFAULT_CHART_H };
      const is_metric = key in (props.dash.metrics || {});
      return { w: DEFAULT_W, h: is_metric ? DEFAULT_CHART_H : DEFAULT_IMAGE_H };
    }

    // ── Watchers ────────────────────────────────────────────────────

    // Reset ONLY on actual selection change (string key avoids array-reference trap)
    watch(
      () => `${props.sel_project?.id ?? ''}_${props.sel_run?.id ?? ''}`,
      () => { card_order.value = []; card_sizes.value = {}; }
    );

    // Sync card_order when available keys change (add new / remove gone / restore saved)
    watch(
      () => {
        const m = Object.keys(props.dash.metrics || {});
        const i = (props.dash.image_cards || []).map(c => c.key);
        return [...m, ...i];
      },
      new_keys => {
        if (!new_keys.length) return;

        if (card_order.value.length === 0) {
          // First data for this selection — try to restore saved layout
          const saved = load_layout(new_keys);
          if (saved) {
            card_order.value = saved.order;
            const merged = {};
            for (const key of saved.order) {
              merged[key] = saved.sizes[key] || default_size(key);
            }
            // Restore group sizes (group:: keys are not in order but must be preserved)
            for (const [k, v] of Object.entries(saved.sizes)) {
              if (k.startsWith('group::')) merged[k] = v;
            }
            card_sizes.value = merged;
            return;
          }
          // No saved layout — build defaults
          for (const key of new_keys) {
            card_order.value.push(key);
            card_sizes.value[key] = default_size(key);
          }
          return;
        }

        // Subsequent updates (refresh): add new keys, remove gone, preserve order/sizes
        for (const key of new_keys) {
          if (!card_order.value.includes(key)) {
            card_order.value.push(key);
            card_sizes.value[key] = default_size(key);
          }
        }
        card_order.value = card_order.value.filter(k => new_keys.includes(k));
      },
      { immediate: true }
    );

    // Persist layout whenever order or sizes change
    watch([card_order, card_sizes], save_layout, { deep: true });

    // ── Drag-to-reorder ─────────────────────────────────────────────
    function start_drag(unit_key) {
      dragging_key.value = unit_key;
      document.body.style.userSelect = 'none';
      const on_up = () => {
        if (drag_over_key.value && drag_over_key.value !== dragging_key.value) {
          const units = compute_units(card_order.value);
          const get_flat_keys = uk => {
            const u = units.find(u => u.unit_key === uk);
            return u ? (u.type === 'group' ? u.keys : [u.key]) : [];
          };
          const from_keys = get_flat_keys(dragging_key.value);
          const to_keys   = get_flat_keys(drag_over_key.value);
          if (from_keys.length && to_keys.length) {
            const arr = card_order.value.filter(k => !from_keys.includes(k));
            const to_idx = arr.indexOf(to_keys[0]);
            if (to_idx !== -1) {
              arr.splice(to_idx, 0, ...from_keys);
              card_order.value = arr;
            }
          }
        }
        dragging_key.value  = null;
        drag_over_key.value = null;
        document.body.style.userSelect = '';
        window.removeEventListener('mouseup', on_up);
      };
      window.addEventListener('mouseup', on_up);
    }

    function on_drag_enter(key) {
      if (dragging_key.value) drag_over_key.value = key;
    }

    // ── Within-group drag-to-reorder ─────────────────────────────────
    function start_child_drag(key) {
      dragging_child_key.value = key;
      document.body.style.userSelect = 'none';
      const on_up = () => {
        if (drag_over_child_key.value && drag_over_child_key.value !== dragging_child_key.value) {
          const arr  = [...card_order.value];
          const from = arr.indexOf(dragging_child_key.value);
          const to   = arr.indexOf(drag_over_child_key.value);
          if (from !== -1 && to !== -1) {
            arr.splice(from, 1);
            arr.splice(to, 0, dragging_child_key.value);
            card_order.value = arr;
          }
        }
        dragging_child_key.value  = null;
        drag_over_child_key.value = null;
        document.body.style.userSelect = '';
        window.removeEventListener('mouseup', on_up);
      };
      window.addEventListener('mouseup', on_up);
    }

    function on_child_drag_enter(key) {
      if (dragging_child_key.value) drag_over_child_key.value = key;
    }

    function on_resize(key, dims) {
      card_sizes.value = { ...card_sizes.value, [key]: dims };
    }

    function toggle_collapse(key) {
      const cur = card_sizes.value[key] || default_size(key);
      card_sizes.value = { ...card_sizes.value, [key]: { ...cur, collapsed: !cur.collapsed } };
    }

    // ── Render ──────────────────────────────────────────────────────
    return () => {
      if (props.is_loading) {
        return h('div', { class: 'main-panel' }, [
          h('div', { class: 'loading-row' }, [h('i', { class: 'fa-solid fa-spinner fa-spin' }), 'Loading…']),
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

      if (!card_order.value.length) {
        return h('div', { class: 'main-panel' }, [
          h('div', { class: 'empty-state' }, [
            h('i', { class: 'fa-solid fa-inbox empty-icon' }),
            h('span', { class: 'empty-text' }, 'No data logged yet'),
          ]),
        ]);
      }

      const { metrics, image_cards, downsampled } = props.dash;
      const is_live  = props.sel_run?.status === 'running';
      const img_map  = Object.fromEntries((image_cards || []).map(c => [c.key, c]));

      function render_card(key, { width, height, label_override } = {}) {
        const is_metric = key in (metrics || {});
        const img_card  = img_map[key];
        const label     = label_override ?? (img_card?.label ?? key);
        const sizes     = card_sizes.value[key] || default_size(key);
        return h(DashCard, {
          key,
          card_label:       label,
          is_metric,
          is_dragging:      false,
          is_drag_over:     false,
          width:            width !== undefined ? width : sizes.w,
          height:           height !== undefined ? height : sizes.h,
          collapsed:        !!sizes.collapsed,
          downsampled:      is_metric && downsampled,
          onDragStart:      () => {},
          onDragEnter:      () => {},
          onResize:         dims => on_resize(key, { ...sizes, ...dims }),
          onToggleCollapse: () => toggle_collapse(key),
        }, {
          default: () => is_metric
            ? h(MetricChart, { metric_key: key, datasets: (metrics || {})[key] || [], is_live })
            : h(ImageSlider, { img_key: key, runs: img_card?.runs ?? [] }),
        });
      }

      const units = compute_units(card_order.value);
      const rendered = units.map(unit => {
        if (unit.type === 'single') {
          const key   = unit.key;
          const sizes = card_sizes.value[key] || default_size(key);
          return h(DashCard, {
            key,
            card_label:       img_map[key]?.label ?? key,
            is_metric:        key in (metrics || {}),
            is_dragging:      dragging_key.value  === unit.unit_key,
            is_drag_over:     drag_over_key.value === unit.unit_key,
            width:            sizes.w,
            height:           sizes.h,
            collapsed:        !!sizes.collapsed,
            downsampled:      (key in (metrics || {})) && downsampled,
            onDragStart:      () => start_drag(unit.unit_key),
            onDragEnter:      () => on_drag_enter(unit.unit_key),
            onResize:         dims => on_resize(key, { ...sizes, ...dims }),
            onToggleCollapse: () => toggle_collapse(key),
          }, {
            default: () => key in (metrics || {})
              ? h(MetricChart, { metric_key: key, datasets: (metrics || {})[key] || [], is_live })
              : h(ImageSlider, { img_key: key, runs: img_map[key]?.runs ?? [] }),
          });
        }

        // Group
        const gk          = unit.unit_key;
        const group_sizes = card_sizes.value[gk] || default_size(gk);

        // Collapsed children sink to the bottom; active ones stay on top
        const sorted_keys = [
          ...unit.keys.filter(k => !(card_sizes.value[k] || default_size(k)).collapsed),
          ...unit.keys.filter(k =>  !!(card_sizes.value[k] || default_size(k)).collapsed),
        ];

        const children = sorted_keys.map(key => {
          const csizes = card_sizes.value[key] || default_size(key);
          const is_metric = key in (metrics || {});
          return h(DashCard, {
            key,
            card_label:       key.slice(unit.prefix.length + 1),
            is_metric,
            is_dragging:      dragging_child_key.value  === key,
            is_drag_over:     drag_over_child_key.value === key && dragging_child_key.value !== key,
            width:            csizes.w,
            height:           csizes.h,
            collapsed:        !!csizes.collapsed,
            downsampled:      is_metric && downsampled,
            onDragStart:      () => start_child_drag(key),
            onDragEnter:      () => on_child_drag_enter(key),
            onResize:         dims => on_resize(key, { ...csizes, ...dims }),
            onToggleCollapse: () => toggle_collapse(key),
          }, {
            default: () => is_metric
              ? h(MetricChart, { metric_key: key, datasets: (metrics || {})[key] || [], is_live })
              : h(ImageSlider, { img_key: key, runs: img_map[key]?.runs ?? [] }),
          });
        });

        return h(MetricGroup, {
          key:              gk,
          prefix:           unit.prefix,
          width:            group_sizes.w,
          height:           group_sizes.h ?? DEFAULT_CHART_H,
          collapsed:        !!group_sizes.collapsed,
          is_dragging:      dragging_key.value  === gk,
          is_drag_over:     drag_over_key.value === gk,
          onDragStart:      () => start_drag(gk),
          onDragEnter:      () => on_drag_enter(gk),
          onResizeGroup:    dims => on_resize(gk, { ...group_sizes, ...dims }),
          onToggleCollapse: () => toggle_collapse(gk),
        }, { default: () => children });
      });

      return h('div', { class: 'main-panel' }, [
        h('div', { class: 'cards-grid' }, rendered),
      ]);
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
    const user        = ref(null);
    const projects    = ref([]);
    const sel_project = ref(null);
    const sel_run     = ref(null);
    const is_loading  = ref(false);
    const dash        = ref({ metrics: {}, image_cards: [], downsampled: false });
    const admin_view  = ref(false);

    let _refresh_timer   = null;
    let _refresh_delay   = 5000;
    let _fail_count      = 0;
    let _refresh_run_id  = null;
    let _refresh_proj_id = null;

    async function init() {
      const me = await api('/auth/me');
      if (!me.logged_in) { window.location = '/auth/login'; return; }
      user.value = me;
      await load_projects();
      await restore_from_url() || await restore_last_selection();
    }

    async function load_projects() {
      const list = await api('/api/v1/projects');
      await Promise.all(list.map(async proj => {
        proj.runs = await api(`/api/v1/projects/${proj.id}/runs`).catch(() => []);
      }));
      projects.value = list;
    }

    function save_last_selection() {
      const val = sel_run.value
        ? { proj_id: sel_project.value?.id, run_id: sel_run.value.id }
        : sel_project.value
          ? { proj_id: sel_project.value.id, run_id: null }
          : null;
      if (val) localStorage.setItem('wandb_last_sel', JSON.stringify(val));
      else localStorage.removeItem('wandb_last_sel');
    }

    async function restore_from_url() {
      const params = new URLSearchParams(window.location.search);
      const run_name = params.get('run');
      if (!run_name) return false;
      for (const proj of projects.value) {
        const run = (proj.runs || []).find(r => r.name === run_name);
        if (run) { await _do_select_run(proj, run); return true; }
      }
      return false;
    }

    async function restore_last_selection() {
      try {
        const saved = JSON.parse(localStorage.getItem('wandb_last_sel'));
        if (!saved?.proj_id) return;
        const proj = projects.value.find(p => p.id === saved.proj_id);
        if (!proj) return;
        if (saved.run_id) {
          const run = (proj.runs || []).find(r => r.id === saved.run_id);
          if (run) { await _do_select_run(proj, run); return; }
        }
        await _do_select_project(proj);
      } catch { /* ignore */ }
    }

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
      save_last_selection();
      is_loading.value  = true;
      try { await load_project_dash(proj); } finally { is_loading.value = false; }
      if ((proj.runs || []).some(r => r.status === 'running')) start_project_refresh(proj.id);
    }

    async function _do_select_run(proj, run) {
      stop_refresh();
      sel_project.value = proj;
      sel_run.value     = run;
      save_last_selection();
      is_loading.value  = true;
      try { await load_run_dash(run); } finally { is_loading.value = false; }
      if (run.status === 'running') start_refresh(run.id);
    }

    // ── Dashboard loaders ───────────────────────────────────────────

    async function load_project_dash(proj) {
      const runs = proj.runs || [];
      if (!runs.length) {
        dash.value = { metrics: {}, image_cards: [], downsampled: false };
        return;
      }

      // Fetch metric keys and image keys for every run in parallel
      const [key_sets, img_key_sets] = await Promise.all([
        Promise.all(runs.map(r => api(`/api/v1/runs/${r.id}/metric-keys`).catch(() => []))),
        Promise.all(runs.map(r => api(`/api/v1/runs/${r.id}/image-keys`).catch(() => []))),
      ]);

      const all_metric_keys = [...new Set(key_sets.flat())];
      const all_img_keys    = [...new Set(img_key_sets.flat())];

      // Fetch metrics and per-run images in parallel
      const [metrics_by_run, images_by_run] = await Promise.all([
        all_metric_keys.length
          ? Promise.all(runs.map(r =>
              api(`/api/v1/runs/${r.id}/metrics?keys=${all_metric_keys.join(',')}`).catch(() => ({ metrics: {}, downsampled: false }))
            ))
          : Promise.resolve(runs.map(() => ({ metrics: {}, downsampled: false }))),

        // For each run, fetch images for every image key it has
        Promise.all(runs.map(async (run, ri) => {
          const run_img_keys = img_key_sets[ri];
          if (!run_img_keys.length) return {};
          const result = {};
          await Promise.all(run_img_keys.map(async key => {
            const resp = await api(`/api/v1/runs/${run.id}/images?key=${encodeURIComponent(key)}`).catch(() => ({ images: [] }));
            result[key] = resp.images || [];
          }));
          return result;
        })),
      ]);

      // Build metric grouped series
      const metrics = {};
      let any_downsampled = false;
      for (const key of all_metric_keys) {
        metrics[key] = [];
        metrics_by_run.forEach((resp, idx) => {
          if (resp.downsampled) any_downsampled = true;
          const pts = (resp.metrics || {})[key] || [];
          if (pts.length) metrics[key].push({ label: runs[idx].name, points: pts });
        });
        if (!metrics[key].length) delete metrics[key];
      }

      // Build image_cards: one card per img_key, runs array contains all runs
      const image_cards = [];
      for (const img_key of all_img_keys) {
        image_cards.push({
          key:   img_key,
          label: img_key,
          runs:  runs.map((run, ri) => ({
            label:  run.name,
            images: (images_by_run[ri] || {})[img_key] || [],
          })),
        });
      }

      dash.value = { metrics, image_cards, downsampled: any_downsampled };
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

      const metrics = {};
      for (const [key, pts] of Object.entries(metrics_resp.metrics || {})) {
        metrics[key] = [{ label: run.name, points: pts }];
      }

      const image_cards = [];
      if (img_keys_resp.length) {
        await Promise.all(img_keys_resp.map(async key => {
          const resp = await api(`/api/v1/runs/${run.id}/images?key=${encodeURIComponent(key)}`).catch(() => ({ images: [] }));
          image_cards.push({ key, label: key, runs: [{ label: run.name, images: resp.images || [] }] });
        }));
        // Restore original key order
        image_cards.sort((a, b) => img_keys_resp.indexOf(a.key) - img_keys_resp.indexOf(b.key));
      }

      dash.value = { metrics, image_cards, downsampled: metrics_resp.downsampled };
    }

    // ── Auto-refresh ────────────────────────────────────────────────
    function start_refresh(run_id) {
      _refresh_run_id  = run_id;
      _refresh_proj_id = null;
      _refresh_delay   = 5000;
      _fail_count      = 0;
      schedule_refresh();
    }
    function start_project_refresh(proj_id) {
      _refresh_proj_id = proj_id;
      _refresh_run_id  = null;
      _refresh_delay   = 5000;
      _fail_count      = 0;
      schedule_refresh();
    }
    function schedule_refresh() {
      _refresh_timer = setTimeout(do_refresh, _refresh_delay);
    }
    async function do_refresh() {
      try {
        if (_refresh_run_id && sel_run.value?.id === _refresh_run_id) {
          // ── Run refresh ────────────────────────────────────────────
          const run_data = await api(`/api/v1/runs/${_refresh_run_id}`);
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
          else stop_refresh();

        } else if (_refresh_proj_id && sel_project.value?.id === _refresh_proj_id && !sel_run.value) {
          // ── Project refresh — reload runs then dashboard ───────────
          const proj = projects.value.find(p => p.id === _refresh_proj_id);
          if (!proj) { stop_refresh(); return; }
          proj.runs = await api(`/api/v1/projects/${_refresh_proj_id}/runs`).catch(() => proj.runs);
          await load_project_dash(proj);
          _fail_count    = 0;
          _refresh_delay = 5000;
          if (proj.runs.some(r => r.status === 'running')) schedule_refresh();
          else stop_refresh();
        }
      } catch (_) {
        _fail_count++;
        _refresh_delay = Math.min(60000, 5000 * Math.pow(2, _fail_count));
        schedule_refresh();
      }
    }
    function stop_refresh() {
      clearTimeout(_refresh_timer);
      _refresh_timer   = null;
      _refresh_run_id  = null;
      _refresh_proj_id = null;
    }
    onUnmounted(stop_refresh);

    // ── Delete handlers ─────────────────────────────────────────────
    async function delete_project(proj_id) {
      await api(`/api/v1/projects/${proj_id}`, { method: 'DELETE' });
      if (sel_project.value?.id === proj_id) {
        stop_refresh();
        sel_project.value = null;
        sel_run.value     = null;
        dash.value = { metrics: {}, image_cards: [], downsampled: false };
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
      await load_projects();
    }

    // ── Mobile panel state ───────────────────────────────────────────
    const panel_open = ref(window.innerWidth > 768);
    function is_mobile() { return window.innerWidth <= 768; }
    function toggle_panel() { panel_open.value = !panel_open.value; }
    function close_panel_on_mobile() { if (is_mobile()) panel_open.value = false; }

    // Update panel_open default when window resizes (e.g. rotating device)
    function on_resize() { if (!is_mobile()) panel_open.value = true; }
    window.addEventListener('resize', on_resize);
    onUnmounted(() => window.removeEventListener('resize', on_resize));

    function toggle_theme() {
      const is_light = document.body.classList.toggle('light');
      localStorage.setItem('theme', is_light ? 'light' : 'dark');
    }

    // Apply saved theme before first render
    if (localStorage.getItem('theme') === 'light') document.body.classList.add('light');

    function start_panel_resize(e) {
      const start_x = e.clientX;
      const start_w = parseInt(getComputedStyle(document.documentElement).getPropertyValue('--left-w'));
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

    return () => h('div', { id: 'app-inner', style: 'display:contents' }, [
      h(TopBar, {
        user:         user.value,
        admin_active: admin_view.value,
        onToggleTheme:  toggle_theme,
        onLogout:       () => { window.location = '/auth/logout'; },
        onKeyCopied:    () => {},
        onToggleAdmin:  () => { admin_view.value = !admin_view.value; },
        onTogglePanel:  toggle_panel,
      }),
      admin_view.value
        ? [h(AdminPanel)]
        : [
            // Backdrop — closes panel when tapping outside on mobile
            h('div', {
              class: `panel-backdrop${panel_open.value ? ' open' : ''}`,
              onClick: toggle_panel,
            }),
            h(LeftPanel, {
              projects: projects.value,
              sel_project_id: sel_project.value?.id ?? null,
              sel_run_id:     sel_run.value?.id ?? null,
              class: panel_open.value ? 'open' : '',
              onSelectProject: proj       => { select_project(proj);       close_panel_on_mobile(); },
              onSelectRun:     (proj, run) => { select_run(proj, run);     close_panel_on_mobile(); },
              onDeleteProject: delete_project,
              onDeleteRun:     delete_run,
            }),
            h('div', { class: 'resize-handle lhandle', onMousedown: start_panel_resize }),
            h(MainPanel, {
              dash:        dash.value,
              is_loading:  is_loading.value,
              sel_project: sel_project.value,
              sel_run:     sel_run.value,
            }),
          ],
      h(StatusBar, {
        sel_project: sel_project.value,
        sel_run:     sel_run.value,
      }),
    ]);
  },
});

const app = createApp(App);
app.mount('#app');
window.__app = { reload: () => app._instance?.proxy?.$forceUpdate?.() };
