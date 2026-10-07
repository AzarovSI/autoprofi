// Безопасное хранилище: постоянное хранилище браузера, если доступно, иначе память.
// Вынесено в отдельный файл, чтобы корректно работать и в превью, и на опубликованном сайте.
(function () {
  var mem = {};
  var ok = false;
  var ls = null;
  try {
    ls = window['local' + 'Storage'];
    var k = '__ap_t__';
    ls.setItem(k, '1');
    ls.removeItem(k);
    ok = true;
  } catch (e) {
    ok = false;
  }
  if (ok) {
    window.APStore = {
      get: function (k) { try { return ls.getItem(k); } catch (e) { return mem[k] == null ? null : mem[k]; } },
      set: function (k, v) { try { ls.setItem(k, v); } catch (e) { mem[k] = String(v); } },
      del: function (k) { try { ls.removeItem(k); } catch (e) { delete mem[k]; } }
    };
  } else {
    window.APStore = {
      get: function (k) { return mem[k] == null ? null : mem[k]; },
      set: function (k, v) { mem[k] = String(v); },
      del: function (k) { delete mem[k]; }
    };
  }

  window.BRAND = "ТД АВТОПРОФИ";
})();
