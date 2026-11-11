from django.urls import path

from . import views

urlpatterns = [
    path("files", views.FileListView.as_view()),
    path("files/<int:pk>", views.FileDetailView.as_view()),
    path("files/<int:pk>/pages", views.FilePagesView.as_view()),
    path("files/<int:pk>/pages/<int:page>/retry", views.PageRetryView.as_view()),
]
