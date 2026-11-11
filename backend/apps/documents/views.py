from botocore.exceptions import BotoCoreError, ClientError
from django.db.models import OuterRef, Subquery
from django.shortcuts import get_object_or_404
from rest_framework import generics, status
from rest_framework.response import Response
from rest_framework.views import APIView

from . import services
from .models import File, Job, SegmentRow
from .serializers import FileSerializer, PageSerializer, UploadSerializer
from .storage import S3Storage


def get_storage():
    return S3Storage.from_settings()


def own_files(request):
    """The only way views reach files: always the caller's own, so other users' ids are simply not found (404)."""
    latest_status = Job.objects.filter(file=OuterRef("pk")).order_by("-id").values("status")[:1]
    return File.objects.filter(owner=request.user).annotate(status=Subquery(latest_status)).order_by("-id")


class FileListView(generics.ListAPIView):
    serializer_class = FileSerializer

    def get_queryset(self):
        return own_files(self.request)

    def post(self, request):
        data = UploadSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        try:
            file = services.create_upload(request.user, data.validated_data["file"], get_storage())
        except services.UploadError as e:
            return Response({"detail": e.message}, status=e.status_code)
        except (BotoCoreError, ClientError):
            return Response({"detail": "Storage is unavailable, try again."}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        file.status = Job.Status.QUEUED
        return Response(FileSerializer(file).data, status=status.HTTP_201_CREATED)


class FileDetailView(generics.RetrieveAPIView):
    serializer_class = FileSerializer

    def get_queryset(self):
        return own_files(self.request)


class FilePagesView(APIView):
    def get(self, request, pk):
        file = get_object_or_404(own_files(request), pk=pk)
        pages = SegmentRow.objects.filter(file=file).order_by("index")
        return Response(PageSerializer(pages, many=True).data)
