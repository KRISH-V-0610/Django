"""
IDOR (topic D): Insecure Direct Object Reference.

Two routers over the SAME model:

  vulnerable_router  /api/vulnerable/documents/{id}
      checks "are you logged in?" but never "is this YOURS?" -> user B can read, change
      and delete user A's documents just by changing the number in the URL.

  secure_router      /api/documents/{id}
      every query is filtered by owner=request.auth. A document that is not yours
      behaves exactly like one that does not exist (404), so ids cannot even be probed.
"""
from django.http import Http404
from django.shortcuts import get_object_or_404
from ninja import Router, Schema, Status

from accounts.auth import jwt_auth
from core.security_log import log_event

from .models import Document


class DocumentIn(Schema):
    title: str
    content: str = ""


class DocumentOut(Schema):
    id: int
    owner_id: int
    title: str
    content: str


secure_router = Router(tags=["documents (secure)"], auth=jwt_auth)
vulnerable_router = Router(tags=["documents (VULNERABLE - IDOR demo)"], auth=jwt_auth)


# ----------------------------------------------------------------- secure version
def get_owned_document(request, document_id):
    """The one place that enforces ownership. Used by read, update and delete."""
    document = Document.objects.filter(pk=document_id, owner=request.auth).first()
    if document is None:
        if Document.objects.filter(pk=document_id).exists():
            # exists, but belongs to someone else: that is an attack signal, so log it
            log_event("idor_blocked", request, document_id=document_id)
        raise Http404("Document not found")
    return document


@secure_router.post("", response={201: DocumentOut})
def create_document(request, data: DocumentIn):
    document = Document.objects.create(owner=request.auth, title=data.title, content=data.content)
    return Status(201, document)


@secure_router.get("", response=list[DocumentOut])
def list_my_documents(request):
    return Document.objects.filter(owner=request.auth).order_by("id")


@secure_router.get("/{document_id}", response=DocumentOut)
def read_document(request, document_id: int):
    return get_owned_document(request, document_id)


@secure_router.put("/{document_id}", response=DocumentOut)
def update_document(request, document_id: int, data: DocumentIn):
    document = get_owned_document(request, document_id)
    document.title = data.title
    document.content = data.content
    document.save(update_fields=["title", "content"])
    return document


@secure_router.delete("/{document_id}", response={204: None})
def delete_document(request, document_id: int):
    get_owned_document(request, document_id).delete()
    return Status(204, None)


# ------------------------------------------------------------- vulnerable version
@vulnerable_router.get("/{document_id}", response=DocumentOut)
def vulnerable_read(request, document_id: int):
    return get_object_or_404(Document, pk=document_id)  # BUG: no owner check


@vulnerable_router.put("/{document_id}", response=DocumentOut)
def vulnerable_update(request, document_id: int, data: DocumentIn):
    document = get_object_or_404(Document, pk=document_id)  # BUG: no owner check
    document.title = data.title
    document.content = data.content
    document.save(update_fields=["title", "content"])
    return document


@vulnerable_router.delete("/{document_id}", response={204: None})
def vulnerable_delete(request, document_id: int):
    get_object_or_404(Document, pk=document_id).delete()  # BUG: no owner check
    return Status(204, None)
