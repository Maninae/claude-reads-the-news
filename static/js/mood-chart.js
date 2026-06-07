// Mood chart initialization for /archive/.
//
// Data is injected by Hugo into a <script id="mood-data" type="application/json">
// block. This file reads that blob and renders the chart. Chart.js must be
// loaded before this script.

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

    var isDark = document.documentElement.getAttribute('data-theme') === 'dark';
    var tickColor = isDark ? '#6a6259' : '#9a958e';
    var gridMajor = isDark ? 'rgba(224, 213, 193, 0.08)' : 'rgba(26, 24, 23, 0.06)';
    var gridMinor = isDark ? 'rgba(224, 213, 193, 0.04)' : 'rgba(26, 24, 23, 0.04)';
    var tooltipBg = isDark ? '#2a2520' : '#1a1817';
    var tooltipText = isDark ? '#ddd5c4' : '#f8f7f4';
    var accentColor = isDark ? '#d97a5e' : '#c45d3e';
    var fillColor = isDark ? 'rgba(217, 122, 94, 0.1)' : 'rgba(196, 93, 62, 0.08)';

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
          borderColor: accentColor,
          backgroundColor: fillColor,
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
        // Narrower screens get a squarer chart so the dots stay readable.
        aspectRatio: window.innerWidth < 600 ? 1.4 : 2.5,
        scales: {
          y: {
            min: 0,
            max: 10,
            ticks: {
              color: tickColor,
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
            grid: { color: gridMajor }
          },
          x: {
            ticks: {
              color: tickColor,
              font: { family: 'JetBrains Mono', size: 11 }
            },
            grid: { color: gridMinor }
          }
        },
        plugins: {
          legend: { display: false },
          tooltip: {
            backgroundColor: tooltipBg,
            titleColor: tooltipText,
            bodyColor: tooltipText,
            borderColor: isDark ? '#3a3835' : '#3a3835',
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
