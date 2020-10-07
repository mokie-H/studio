import './image-upload.css';
import { IMAGE_PLACEHOLDER } from '../../constants';
import imageUploadExtension from './image-upload.js';

export const IMAGE_REGEX = /!\[([^\]]*)]\(([^/]+\/([^\s]+))(?:\s=([0-9.]+)x([0-9.]+))*\)/g;
export const SINGLE_IMAGE_REGEX = /!\[([^\]]*)]\(([^/]+\/([^\s]+))(?:\s=([0-9.]+)x([0-9.]+))*\)/;

export const imageMdToParams = imageMd => {
  const match = imageMd.match(SINGLE_IMAGE_REGEX);
  if (!match) {
    return {};
  }
  const description = match[1];
  const filePathWithPlaceholder = match[2];
  const fileNameWithExtension = match[3];
  const width = match[4];
  const height = match[5];

  const imagePath = `/content/storage/${fileNameWithExtension[0]}/${fileNameWithExtension[1]}`;
  const src = filePathWithPlaceholder.replace(IMAGE_PLACEHOLDER, imagePath);
  const checksum = fileNameWithExtension.split('.')[0];

  return { imageMd, imagePath, src, width, height, checksum, alt: description };
};

export const paramsToImageMd = ({ src, alt, width, height }) => {
  src = src.split('/').lastItem;
  if (width && width !== 'auto' && height && height !== 'auto') {
    return `![${alt}](${IMAGE_PLACEHOLDER}/${src} =${width}x${height})`;
  } else {
    return `![${alt}](${IMAGE_PLACEHOLDER}/${src})`;
  }
};

export const imageMdToImageFieldHTML = imageMd =>
  `<span is='markdown-image-field'>${imageMd}</span>`;
export const paramsToImageFieldHTML = params => imageMdToImageFieldHTML(paramsToImageMd(params));

export default imageUploadExtension;
