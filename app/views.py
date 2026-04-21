import os
import logging
from django.shortcuts import render, redirect
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db import transaction
from .forms import UserRegistrationForm, DocumentUploadForm
from .models import Document, QueryLog
from .utils.file_handler import get_user_lock
from .utils.validator import validate_file
from .utils.chunking import extract_and_chunk_file
from .utils.faiss_store import add_documents_to_store

logger = logging.getLogger(__name__)

def register(request):
    if request.method == 'POST':
        form = UserRegistrationForm(request.POST)
        if form.is_valid():
            user = form.save(commit=False)
            user.set_password(form.cleaned_data['password'])
            user.save()
            login(request, user)
            return redirect('app:dashboard')
    else:
        form = UserRegistrationForm()
    return render(request, 'app/register.html', {'form': form})

@login_required
def dashboard(request):
    documents = Document.objects.filter(user=request.user).order_by('-uploaded_at')
    return render(request, 'app/dashboard.html', {'documents': documents})

@login_required
def upload_document(request):
    if request.method == 'POST':
        form = DocumentUploadForm(request.POST, request.FILES)
        if form.is_valid():
            uploaded_file = request.FILES['file']
            
            try:
                # Acquire user-specific lock to prevent race conditions during parallel uploads
                lock = get_user_lock(request.user.id)
                with lock:
                    # Handle file validation (memory-safe and thorough)
                    is_valid, msg, file_hash = validate_file(uploaded_file, request.user)
                    if not is_valid:
                        messages.error(request, msg)
                        return redirect('app:upload')

                    # Transaction control ensures FAISS output and DB status are aligned
                    with transaction.atomic():
                        document = form.save(commit=False)
                        document.user = request.user
                        document.file_hash = file_hash  # Save duplicate hash
                        document.is_processed = False
                        document.save()
                        
                        # Extract, Chunk, Embed and Store
                        chunks = extract_and_chunk_file(document.file.path)
                        add_documents_to_store(request.user.id, document.id, chunks)
                        
                        # Status update on success
                        document.is_processed = True
                        document.save()
                        
                        messages.success(request, f"Document '{document.file.name}' uploaded and processed successfully!")
                    return redirect('app:dashboard')
                    
            except Exception as e:
                logger.error(f"An error occurred during upload: {str(e)}")
                messages.error(request, f"An error occurred during upload: {str(e)}")
                return redirect('app:upload')
    else:
        form = DocumentUploadForm()
        
    return render(request, 'app/upload.html', {'form': form})

@login_required
def delete_document(request, doc_id):
    if request.method == 'POST':
        try:
            with transaction.atomic():
                document = Document.objects.get(id=doc_id, user=request.user)
                
                # Remove associated FAISS files and metadata
                from .utils.metadata_handler import get_faiss_store_dir
                store_dir = get_faiss_store_dir(request.user.id)
                
                faiss_file = os.path.join(store_dir, f"{document.id}.faiss")
                pkl_file = os.path.join(store_dir, f"{document.id}.pkl")
                meta_file = os.path.join(store_dir, f"{document.id}_meta.json")
                
                for file_path in [faiss_file, pkl_file, meta_file]:
                    if os.path.exists(file_path):
                        try:
                            os.remove(file_path)
                            logger.info(f"Deleted vector index file: {file_path}")
                        except Exception as e:
                            logger.error(f"Failed to delete {file_path}: {str(e)}")
                
                # Delete file from storage safely
                if document.file and os.path.exists(document.file.path):
                    try:
                        os.remove(document.file.path)
                        logger.info(f"Deleted original file: {document.file.path}")
                    except Exception as e:
                        logger.error(f"Failed to delete original file {document.file.path}: {str(e)}")
                    
                # Delete DB record
                document.delete()
                messages.success(request, "Document deleted successfully.")
        except Document.DoesNotExist:
            messages.error(request, "Document not found.")
        except Exception as e:
            logger.error(f"Error during document deletion: {str(e)}")
            messages.error(request, f"Error deleting document: {str(e)}")
            
    return redirect('app:dashboard')


