API Development Guide
=====================

This guide covers best practices for developing and extending the Shizuha Drive API.

API Architecture
----------------

Shizuha Drive uses Django REST Framework (DRF) for its API layer:

.. code-block:: text

   Request → URL Router → View → Serializer → Model → Database
                          ↓
                     Response

Key Components
^^^^^^^^^^^^^^

* **Views**: Handle HTTP requests and responses
* **Serializers**: Validate input and format output
* **Models**: Database schema and business logic
* **Permissions**: Authentication and authorization

Creating a New Endpoint
-----------------------

1. Define the Model
^^^^^^^^^^^^^^^^^^^

.. code-block:: python

   # drive/models.py
   from django.db import models
   import uuid

   class Tag(models.Model):
       id = models.UUIDField(primary_key=True, default=uuid.uuid4)
       name = models.CharField(max_length=100)
       color = models.CharField(max_length=7, default='#808080')
       owner_id = models.UUIDField(db_index=True)
       created_at = models.DateTimeField(auto_now_add=True)

       class Meta:
           unique_together = ['owner_id', 'name']

2. Create the Serializer
^^^^^^^^^^^^^^^^^^^^^^^^

.. code-block:: python

   # drive/serializers.py
   from rest_framework import serializers
   from .models import Tag

   class TagSerializer(serializers.ModelSerializer):
       class Meta:
           model = Tag
           fields = ['id', 'name', 'color', 'created_at']
           read_only_fields = ['id', 'created_at']

       def create(self, validated_data):
           # Add owner_id from request
           validated_data['owner_id'] = self.context['request'].user_id
           return super().create(validated_data)

3. Create the View
^^^^^^^^^^^^^^^^^^

.. code-block:: python

   # drive/views.py
   from rest_framework import viewsets, permissions
   from .models import Tag
   from .serializers import TagSerializer

   class TagViewSet(viewsets.ModelViewSet):
       serializer_class = TagSerializer
       permission_classes = [permissions.IsAuthenticated]

       def get_queryset(self):
           return Tag.objects.filter(owner_id=self.request.user_id)

4. Register the URL
^^^^^^^^^^^^^^^^^^^

.. code-block:: python

   # drive/urls.py
   from rest_framework.routers import DefaultRouter
   from .views import TagViewSet

   router = DefaultRouter()
   router.register('tags', TagViewSet, basename='tag')

   urlpatterns = router.urls

Authentication
--------------

JWT Middleware
^^^^^^^^^^^^^^

Requests are authenticated via JWT token in the Authorization header:

.. code-block:: python

   # drivemanager/middleware.py
   import jwt
   from django.conf import settings

   class JWTAuthenticationMiddleware:
       def __init__(self, get_response):
           self.get_response = get_response

       def __call__(self, request):
           auth_header = request.META.get('HTTP_AUTHORIZATION', '')

           if auth_header.startswith('Bearer '):
               token = auth_header[7:]
               try:
                   payload = jwt.decode(
                       token,
                       settings.JWT_SECRET,
                       algorithms=['HS256']
                   )
                   request.user_id = payload.get('sub')
                   request.user_email = payload.get('email')
               except jwt.InvalidTokenError:
                   request.user_id = None

           return self.get_response(request)

Custom Permission Class
^^^^^^^^^^^^^^^^^^^^^^^

.. code-block:: python

   # drive/permissions.py
   from rest_framework import permissions

   class IsFileOwner(permissions.BasePermission):
       """Allow access only to file owner."""

       def has_object_permission(self, request, view, obj):
           return str(obj.owner_id) == str(request.user_id)

   class CanAccessShare(permissions.BasePermission):
       """Allow access via share link."""

       def has_permission(self, request, view):
           share_token = request.query_params.get('share_token')
           if share_token:
               # Validate share token
               return Share.objects.filter(
                   token=share_token,
                   is_active=True
               ).exists()
           return False

Error Handling
--------------

Standard Error Responses
^^^^^^^^^^^^^^^^^^^^^^^^

Use consistent error format:

.. code-block:: python

   from rest_framework.exceptions import APIException
   from rest_framework import status

   class QuotaExceeded(APIException):
       status_code = status.HTTP_507_INSUFFICIENT_STORAGE
       default_detail = 'Storage quota exceeded'
       default_code = 'quota_exceeded'

   class FileNotFound(APIException):
       status_code = status.HTTP_404_NOT_FOUND
       default_detail = 'File not found'
       default_code = 'file_not_found'

Custom Exception Handler
^^^^^^^^^^^^^^^^^^^^^^^^

.. code-block:: python

   # drivemanager/exception_handler.py
   from rest_framework.views import exception_handler

   def custom_exception_handler(exc, context):
       response = exception_handler(exc, context)

       if response is not None:
           response.data['status_code'] = response.status_code
           response.data['error'] = exc.default_code if hasattr(exc, 'default_code') else 'error'

       return response

Pagination
----------

Configure pagination for list endpoints:

.. code-block:: python

   # drivemanager/settings.py
   REST_FRAMEWORK = {
       'DEFAULT_PAGINATION_CLASS': 'rest_framework.pagination.PageNumberPagination',
       'PAGE_SIZE': 50,
   }

Custom Pagination
^^^^^^^^^^^^^^^^^

.. code-block:: python

   from rest_framework.pagination import PageNumberPagination

   class FilePagination(PageNumberPagination):
       page_size = 50
       page_size_query_param = 'page_size'
       max_page_size = 100

   class FileViewSet(viewsets.ModelViewSet):
       pagination_class = FilePagination

Filtering
---------

Use django-filter for query filtering:

.. code-block:: python

   from django_filters import rest_framework as filters

   class FileFilter(filters.FilterSet):
       name = filters.CharFilter(lookup_expr='icontains')
       file_type = filters.CharFilter()
       min_size = filters.NumberFilter(field_name='size', lookup_expr='gte')
       max_size = filters.NumberFilter(field_name='size', lookup_expr='lte')
       created_after = filters.DateTimeFilter(field_name='created_at', lookup_expr='gte')

       class Meta:
           model = File
           fields = ['name', 'file_type', 'folder']

   class FileViewSet(viewsets.ModelViewSet):
       filterset_class = FileFilter

File Uploads
------------

Handling Large Files
^^^^^^^^^^^^^^^^^^^^

.. code-block:: python

   from rest_framework.parsers import MultiPartParser, FormParser

   class FileUploadView(APIView):
       parser_classes = [MultiPartParser, FormParser]

       def post(self, request):
           file = request.FILES.get('file')
           if not file:
               return Response(
                   {'error': 'No file provided'},
                   status=status.HTTP_400_BAD_REQUEST
               )

           # Check quota before upload
           quota = StorageQuota.objects.get(owner_id=request.user_id)
           if quota.used_bytes + file.size > quota.max_bytes:
               raise QuotaExceeded()

           # Save file
           file_obj = File.objects.create(
               name=file.name,
               file=file,
               size=file.size,
               owner_id=request.user_id
           )

           # Trigger async processing
           process_file_upload.delay(str(file_obj.id))

           return Response(
               FileSerializer(file_obj).data,
               status=status.HTTP_201_CREATED
           )

Streaming Downloads
^^^^^^^^^^^^^^^^^^^

.. code-block:: python

   from django.http import StreamingHttpResponse
   from django.core.files.storage import default_storage

   class FileDownloadView(APIView):
       def get(self, request, file_id):
           file_obj = get_object_or_404(File, id=file_id)

           # Check permission
           if str(file_obj.owner_id) != str(request.user_id):
               raise PermissionDenied()

           # Stream file
           file_handle = default_storage.open(file_obj.file.name, 'rb')

           response = StreamingHttpResponse(
               file_handle,
               content_type=file_obj.mime_type
           )
           response['Content-Disposition'] = f'attachment; filename="{file_obj.name}"'
           response['Content-Length'] = file_obj.size

           return response

API Versioning
--------------

For future API changes:

.. code-block:: python

   # drivemanager/settings.py
   REST_FRAMEWORK = {
       'DEFAULT_VERSIONING_CLASS': 'rest_framework.versioning.URLPathVersioning',
       'DEFAULT_VERSION': 'v1',
       'ALLOWED_VERSIONS': ['v1', 'v2'],
   }

   # urls.py
   urlpatterns = [
       path('api/v1/', include('drive.urls')),
       path('api/v2/', include('drive.urls_v2')),
   ]

Testing APIs
------------

.. code-block:: python

   from rest_framework.test import APITestCase

   class TagAPITest(APITestCase):
       def setUp(self):
           self.client.credentials(
               HTTP_AUTHORIZATION='Bearer ' + self.get_token()
           )

       def test_create_tag(self):
           response = self.client.post('/api/tags/', {
               'name': 'Important',
               'color': '#ff0000'
           })
           self.assertEqual(response.status_code, 201)
           self.assertEqual(response.data['name'], 'Important')

       def test_list_tags(self):
           Tag.objects.create(name='Tag1', owner_id=self.user_id)
           Tag.objects.create(name='Tag2', owner_id=self.user_id)

           response = self.client.get('/api/tags/')
           self.assertEqual(response.status_code, 200)
           self.assertEqual(len(response.data), 2)

Documentation
-------------

Use drf-spectacular for OpenAPI documentation:

.. code-block:: python

   # Install: pip install drf-spectacular

   # settings.py
   REST_FRAMEWORK = {
       'DEFAULT_SCHEMA_CLASS': 'drf_spectacular.openapi.AutoSchema',
   }

   SPECTACULAR_SETTINGS = {
       'TITLE': 'Shizuha Drive API',
       'VERSION': '1.0.0',
   }

   # urls.py
   from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

   urlpatterns = [
       path('api/schema/', SpectacularAPIView.as_view(), name='schema'),
       path('api/docs/', SpectacularSwaggerView.as_view(url_name='schema')),
   ]

Add schema annotations:

.. code-block:: python

   from drf_spectacular.utils import extend_schema, OpenApiParameter

   class FileViewSet(viewsets.ModelViewSet):
       @extend_schema(
           summary="List files",
           description="Returns a paginated list of files in the specified folder",
           parameters=[
               OpenApiParameter(
                   name='folder',
                   description='Filter by folder ID',
                   required=False,
                   type=str
               ),
           ]
       )
       def list(self, request):
           return super().list(request)
