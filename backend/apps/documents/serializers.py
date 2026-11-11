from rest_framework import serializers

from .models import File, SegmentRow


class UploadSerializer(serializers.Serializer):
    file = serializers.FileField()  # missing or empty files are rejected here


class FileSerializer(serializers.ModelSerializer):
    status = serializers.CharField(read_only=True)  # latest job's status, annotated by the view

    class Meta:
        model = File
        fields = ["id", "name", "media_type", "size_bytes", "uploaded_at", "status"]


class PageSerializer(serializers.ModelSerializer):
    page = serializers.IntegerField(source="index")

    class Meta:
        model = SegmentRow
        fields = ["page", "status", "content_kind", "error", "attempts"]
