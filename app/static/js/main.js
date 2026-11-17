/* SmartQueue — shared interaction behaviour
   No dependencies, no network calls, no invented live updates. */
(function () {
    'use strict';

    document.addEventListener('DOMContentLoaded', function () {
        markIconsDecorative();
        wireConfirmations();
        wireSubmitGuards();
        wireQrFallbacks();
        applyDateFloors();
        wireCounters();
        syncButtonDisabledState();
    });

    /* Icons are decorative — the adjacent text always carries the meaning. */
    function markIconsDecorative() {
        document.querySelectorAll('.bi').forEach(function (icon) {
            if (!icon.hasAttribute('aria-hidden')) {
                icon.setAttribute('aria-hidden', 'true');
            }
        });
    }

    /* Confirmation dialogs for actions that change queue state.
       Any form (or submit button) may carry data-confirm="message". */
    function wireConfirmations() {
        document.querySelectorAll('form[data-confirm]').forEach(function (form) {
            form.addEventListener('submit', function (event) {
                var message = form.getAttribute('data-confirm');
                if (message && !window.confirm(message)) {
                    event.preventDefault();
                    event.stopImmediatePropagation();
                }
            });
        });
    }

    function wireSubmitGuards() {
        function guard(form) {
            form.addEventListener('submit', function (event) {
                if (event.defaultPrevented) { return; }
                if (form.dataset.sqSubmitting === '1') {
                    event.preventDefault();
                    return;
                }
                // Let the browser run validation first.
                if (typeof form.checkValidity === 'function' && !form.checkValidity()) {
                    return;
                }
                form.dataset.sqSubmitting = '1';

                var buttons = form.querySelectorAll('button[type="submit"], input[type="submit"]');
                buttons.forEach(function (button) {
                    button.classList.add('is-loading');
                    button.setAttribute('aria-busy', 'true');
                    button.disabled = true;
                    if (button.dataset.loadingLabel) {
                        button.setAttribute('data-sq-label', button.textContent.trim());
                    }
                });
                form.setAttribute('aria-busy', 'true');
            });
        }

        document.querySelectorAll('form').forEach(guard);

        // Back/forward cache restore: never leave a page stuck in loading.
        window.addEventListener('pageshow', function () {
            document.querySelectorAll('form[data-sq-submitting="1"]').forEach(function (form) {
                form.dataset.sqSubmitting = '0';
                form.removeAttribute('aria-busy');
            });
            document.querySelectorAll('.btn.is-loading').forEach(function (button) {
                button.classList.remove('is-loading');
                button.removeAttribute('aria-busy');
                button.disabled = false;
            });
        });
    }

    /* If a QR image cannot load (auth expiry, serverless storage issue),
       replace it with a readable fallback instead of a broken image. */
    function wireQrFallbacks() {
        document.querySelectorAll('img[data-qr-image]').forEach(function (img) {
            var replace = function () {
                var host = img.closest('.sq-qr');
                if (!host) { return; }
                var token = img.getAttribute('data-token') || '';
                var fallback = document.createElement(host.classList.contains('sq-qr-sm') ? 'span' : 'div');

                if (host.classList.contains('sq-qr-sm')) {
                    // Table thumbnail: keep the row compact.
                    fallback.className = 'sq-muted-xs';
                    fallback.textContent = 'QR unavailable';
                    fallback.title = 'QR code unavailable for token ' + token;
                } else {
                    fallback.className = 'sq-qr-fallback';
                    fallback.setAttribute('role', 'img');
                    fallback.setAttribute('aria-label', 'QR code unavailable. Use token ' + token + '.');
                    fallback.innerHTML =
                        '<i class="bi bi-qr-code" aria-hidden="true"></i>' +
                        '<span>QR unavailable.<br>Use token ' + token + '</span>';
                }
                host.replaceWith(fallback);
            };
            img.addEventListener('error', replace);
            // A cached failure may fire before this listener attached.
            if (img.complete && img.naturalWidth === 0) { replace(); }
        });
    }

    /* Booking date: cannot pick a date in the past (server still validates). */
    function applyDateFloors() {
        var today = new Date();
        var iso = new Date(today.getTime() - today.getTimezoneOffset() * 60000)
            .toISOString().slice(0, 10);
        document.querySelectorAll('input[type="date"][data-no-past]').forEach(function (input) {
            input.setAttribute('min', iso);
        });
    }

    /* Optional character counters for textareas with data-counter="200". */
    function wireCounters() {
        document.querySelectorAll('textarea[data-counter]').forEach(function (field) {
            var max = parseInt(field.getAttribute('data-counter'), 10);
            if (!max) { return; }
            var counter = document.createElement('div');
            counter.className = 'sq-counter';
            counter.setAttribute('aria-live', 'polite');
            field.insertAdjacentElement('afterend', counter);

            var update = function () {
                var used = field.value.length;
                counter.textContent = used + ' / ' + max + ' characters';
                counter.classList.toggle('is-near', used > max * 0.9);
            };
            field.addEventListener('input', update);
            update();
        });
    }

    /* Keep "no queue" actions visibly unavailable rather than silently inert. */
    function syncButtonDisabledState() {
        document.querySelectorAll('[data-disabled-reason]').forEach(function (element) {
            element.addEventListener('click', function (event) {
                if (element.getAttribute('aria-disabled') === 'true') {
                    event.preventDefault();
                    event.stopImmediatePropagation();
                }
            });
        });
    }
})();
