"""Custom middleware for Shizuha Drive"""

import os


class ScriptNameMiddleware:
    """
    Middleware to handle SCRIPT_NAME when behind a reverse proxy.
    This ensures URLs are generated correctly when the app is served
    under a prefix like /drive/

    The ingress (shizuha-ingress-routes.yaml) routes /drive/... into the
    service and strips the /drive segment with ``rewrite-target: /$1``, so
    Django would otherwise never see it — and DRF's build_absolute_uri()
    (used for pagination next/previous) would emit a prefix-less, SPA-bound
    URL. Set SCRIPT_NAME on the request so Django's request.path and all
    absolute-URI generation carry the public mount prefix.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Prefer an explicit proxy-provided header when the proxy sets it
        # (e.g. X-Script-Name); otherwise fall back to the configured public
        # mount prefix for this service (DRIVE_URL_PREFIX). DRV-90 acceptance:
        # the default is NO prefix so a dev/local run served at the root path
        # keeps root-path URLs; the production deployment sets
        # DRIVE_URL_PREFIX=/drive to match the ingress mount.
        prefix = request.META.get('HTTP_X_SCRIPT_NAME', '') or os.environ.get(
            'DRIVE_URL_PREFIX', ''
        )
        if prefix:
            # Strip a trailing slash so '/drive' composes with PATH_INFO the
            # same way nginx would.
            prefix = prefix.rstrip('/')
            # request.path is computed from the WSGI environ at construction
            # (before middleware), so META['SCRIPT_NAME'] alone would NOT be
            # picked up by DRF's build_absolute_uri(). Rewrite request.path so
            # every absolute-URI generator (pagination next/previous, etc.)
            # carries the public mount prefix.
            path = request.path
            if not path.startswith(prefix + '/'):
                request.path = prefix + path

        return self.get_response(request)
