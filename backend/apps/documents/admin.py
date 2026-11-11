from django.contrib import admin

from .models import ChunkRow, File, Job, SegmentRow

admin.site.register([File, Job, SegmentRow, ChunkRow])
