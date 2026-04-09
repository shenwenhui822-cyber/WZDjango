from django.urls import path

from portal.routes.api import api_urlpatterns
from portal.routes.web import web_urlpatterns

app_name = "portal"

urlpatterns = [*web_urlpatterns, *api_urlpatterns]
