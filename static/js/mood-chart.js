// Mood chart initialization for /archive/.
//
// Data is injected by Hugo into a <script id="mood-data" type="application/json">
// block (not executable under CSP). This file — loaded from /js/ which matches
// the CSP script-src 'self' allowlist — reads that blob and renders the chart.
// Chart.js must be loaded before this script (the archive template includes
// the jsDelivr <script> tag immediately before this one).

(function () {
  function init() {
    var dataEl = document.getElementById('mood-data');
    var canvas = document.getElementById('moodChart');
    if (!dataEl || !canvas || typeof Chart === 'undefined') {
      return;
    }

    var data;
    try {
      data = JSON.parse(dataEl.textContent);
    } catch (e) {
      return;
    }
    if (!Array.isArray(data) || data.length === 0) {
      return;
    }

    var labels = data.map(function (d) { return d.date; });
    var scores = data.map(function (d) { return d.score; });
    var colors = data.map(function (d) { return d.color; });

    new Chart(canvas.getContext('2d'), {
      type: 'line',
      data: {
        labels: labels,
        datasets: [{
          label: 'Mood Score',
          data: scores,
          borderColor: '#c45d3e',
          backgroundColor: 'rgba(196, 93, 62, 0.1)',
          borderWidth: 2,
          pointBackgroundColor: colors,
          pointBorderColor: colors,
          pointRadius: 6,
          pointHoverRadius: 8,
          tension: 0.3,
          fill: true
        }]
      },
      options: {
        responsive: true,
        maintainAspectRatio: true,
        aspectRatio: 2.5,
        scales: {
          y: {
            min: 0,
            max: 10,
            ticks: {
              color: '#4a4a4a',
              font: { family: 'JetBrains Mono', size: 11 },
              stepSize: 2,
              callback: function (value) {
                var moodLabels = {
                  0: 'Despair',
                  2: 'Dread',
                  4: 'Uneasy',
                  6: 'Watchful',
                  8: 'Hopeful',
                  10: 'Serene'
                };
                return moodLabels[value] || '';
              }
            },
            grid: { color: 'rgba(74, 74, 74, 0.3)' }
          },
          x: {
            ticks: {
              color: '#4a4a4a',
              font: { family: 'JetBrains Mono', size: 11 }
            },
            grid: { color: 'rgba(74, 74, 74, 0.15)' }
          }
        },
        plugins: {
          legend: { display: false },
          tooltip: {
            backgroundColor: '#1a1a1a',
            titleColor: '#e0d5c1',
            bodyColor: '#e0d5c1',
            borderColor: '#4a4a4a',
            borderWidth: 1,
            titleFont: { family: 'Source Serif 4' },
            bodyFont: { family: 'JetBrains Mono', size: 12 },
            callbacks: {
              title: function (items) {
                return data[items[0].dataIndex].title;
              },
              label: function (item) {
                return 'Mood: ' + item.raw + '/10';
              }
            }
          }
        }
      }
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
