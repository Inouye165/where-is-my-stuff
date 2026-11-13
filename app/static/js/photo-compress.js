/**
 * Client-Side Image Compression & Upload Helper
 */
import { apiUpload } from './api.js';

export async function compressImageFile(file, maxDim = 1600, quality = 0.82) {
  if (!file || !file.type || !file.type.startsWith('image/')) return file;
  if (file.size < 400 * 1024) return file;

  let objectUrl = null;
  try {
    let imgBitmap;
    if (typeof createImageBitmap === 'function') {
      imgBitmap = await createImageBitmap(file);
    } else {
      imgBitmap = await new Promise((resolve, reject) => {
        const img = new Image();
        objectUrl = URL.createObjectURL(file);
        img.onload = () => resolve(img);
        img.onerror = reject;
        img.src = objectUrl;
      });
    }

    let targetWidth = imgBitmap.width;
    let targetHeight = imgBitmap.height;
    if (targetWidth > maxDim || targetHeight > maxDim) {
      if (targetWidth > targetHeight) {
        targetHeight = Math.round((targetHeight * maxDim) / targetWidth);
        targetWidth = maxDim;
      } else {
        targetWidth = Math.round((targetWidth * maxDim) / targetHeight);
        targetHeight = maxDim;
      }
    }

    const canvas = document.createElement('canvas');
    canvas.width = targetWidth;
    canvas.height = targetHeight;
    const ctx = canvas.getContext('2d');
    ctx.drawImage(imgBitmap, 0, 0, targetWidth, targetHeight);
    if (typeof imgBitmap.close === 'function') {
      imgBitmap.close();
    }

    const blob = await new Promise(resolve => canvas.toBlob(resolve, 'image/jpeg', quality));
    if (!blob) return file;

    const baseName = file.name.replace(/\.[^/.]+$/, '');
    return new File([blob], `${baseName}.jpg`, { type: 'image/jpeg' });
  } catch {
    return file;
  } finally {
    if (objectUrl) URL.revokeObjectURL(objectUrl);
  }
}

export async function uploadPhotoAndGetPath(rawFile) {
  const compressed = await compressImageFile(rawFile);
  const fd = new FormData();
  fd.append('file', compressed);
  const data = await apiUpload('/api/v1/photos/upload', fd, 'Photo upload failed.');
  return data.original_path;
}

