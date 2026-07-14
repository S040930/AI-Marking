export const DOCX_MIME_TYPE =
  'application/vnd.openxmlformats-officedocument.wordprocessingml.document';

export const DOCUMENT_DROPZONE_ACCEPT = {
  'application/pdf': ['.pdf'],
  [DOCX_MIME_TYPE]: ['.docx'],
};

export const DOCUMENT_INPUT_ACCEPT = `application/pdf,${DOCX_MIME_TYPE},.pdf,.docx`;
