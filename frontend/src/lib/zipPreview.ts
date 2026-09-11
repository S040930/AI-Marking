/**
 * ZIP 上传前的只读预览分析。
 *
 * 分类规则镜像后端 `app/services/submission_zip.py`(报告恰好一份、
 * 代码文件名以小题号结尾、其余为数据集);此处仅作上传前提示,后端仍是
 * 权威校验。只解析中央目录(文件名 + 原始大小),不解压成员内容,
 * 内存占用与 ZIP 压缩体积同阶。
 */

/** 与后端 document_storage.CODE_EXTENSIONS 保持一致 */
export const CODE_EXTENSIONS = [
  '.py', '.ipynb', '.r', '.java', '.c', '.cc', '.cpp', '.cxx',
  '.h', '.hh', '.hpp', '.hxx',
] as const;

/** 与后端 submission_zip.FORBIDDEN_INPUT_EXTENSIONS 保持一致 */
export const FORBIDDEN_INPUT_EXTENSIONS = [
  '.py', '.ipynb', '.r', '.java', '.c', '.cc', '.cpp', '.cxx',
  '.h', '.hh', '.hpp', '.hxx',
  '.app', '.bin', '.com', '.dll', '.dylib', '.exe', '.jar', '.o', '.so',
  '.sh', '.bash', '.zsh', '.bat', '.cmd', '.ps1', '.pl', '.rb', '.go',
  '.rs', '.swift', '.kt', '.m', '.mm',
  '.zip', '.tar', '.gz', '.tgz', '.bz2', '.xz', '.rar', '.7z',
] as const;

/** 与后端 submission_zip 各上限常量保持一致 */
export const ZIP_LIMITS = {
  zipBytes: 150 * 1024 * 1024,
  members: 200,
  uncompressedBytes: 150 * 1024 * 1024,
  reportBytes: 50 * 1024 * 1024,
  codeFiles: 20,
  codeFileBytes: 20 * 1024 * 1024,
  codeTotalBytes: 100 * 1024 * 1024,
  datasetFiles: 5,
  datasetFileBytes: 50 * 1024 * 1024,
  datasetTotalBytes: 100 * 1024 * 1024,
} as const;

const IGNORED_BASENAMES = new Set(['.DS_Store', 'Thumbs.db', 'desktop.ini']);
/**
 * 镜像后端 submission_zip._TRAILING_NUMBER_PATTERN:严格取文件名尾部的
 * 数字组推导小题号。不能放宽为"文件名内第一个数字"——CW1_3.py/q1_v2.py
 * 会错误映射到 1,评分证据随之张冠李戴。
 */
const TRAILING_NUMBER_PATTERN = /(\d+)$/;

/** CW1_3.py → 3,q1_v2.py → 2,task12.py → 12;无尾部数字返回 null。 */
function zipQuestionNumber(basename: string): number | null {
  const dot = basename.lastIndexOf('.');
  const stem = dot === -1 ? basename : basename.slice(0, dot);
  const match = TRAILING_NUMBER_PATTERN.exec(stem);
  if (!match) return null;
  const number = Number(match[1]);
  return number >= 1 ? number : null;
}

export interface ZipEntry {
  name: string;
  size: number;
}

export interface ZipCodeEntry extends ZipEntry {
  questionNumber: number;
}

export interface ZipSkippedEntry {
  name: string;
  reason: 'junk';
}

export interface ZipPreview {
  report: ZipEntry | null;
  code: ZipCodeEntry[];
  datasets: ZipEntry[];
  skipped: ZipSkippedEntry[];
  /** 阻断上传的问题;非空时应禁用提交 */
  errors: string[];
}

/** 嗅探上传文件类型;既非 .pdf 也非 .zip 返回 null。 */
export function sniffUploadKind(filename: string): 'pdf' | 'zip' | null {
  const suffix = filename.toLowerCase().split('.').pop() ?? '';
  if (suffix === 'pdf') return 'pdf';
  if (suffix === 'zip') return 'zip';
  return null;
}

/** 扁平化成员名;垃圾文件/目录条目返回 null;非法名抛 Error(消息面向用户)。 */
function flattenMemberName(rawName: string): string | null {
  const name = rawName.replace(/\\/g, '/');
  if (name.endsWith('/')) {
    // 归档工具(如 macOS Archive Utility)会为打包的文件夹写入显式目录条目
    return null;
  }
  const segments = name.split('/').filter((s) => s !== '' && s !== '.');
  if (segments.length === 0) return null;
  if (name.startsWith('/') || /^[A-Za-z]:/.test(name)) {
    throw new Error(`ZIP 成员不能是绝对路径:${rawName}`);
  }
  if (segments.some((s) => s === '..')) {
    throw new Error(`ZIP 成员存在路径穿越:${rawName}`);
  }
  if (segments[0].startsWith('__MACOSX') || segments[0].startsWith('__')) {
    return null;
  }
  const basename = segments[segments.length - 1];
  if (IGNORED_BASENAMES.has(basename) || basename.startsWith('._')) {
    return null;
  }
  if (basename.startsWith('.')) {
    throw new Error(`ZIP 成员不能是隐藏文件:${rawName}`);
  }
  return basename;
}

/**
 * 解析 ZIP 中央目录并按后端规则分类;始终返回可渲染的 ZipPreview,
 * 阻断性问题进 `errors`。ZIP 结构损坏时抛 Error(由调用方提示重新选择)。
 */
export async function analyzeZip(file: File): Promise<ZipPreview> {
  const preview: ZipPreview = {
    report: null,
    code: [],
    datasets: [],
    skipped: [],
    errors: [],
  };
  if (file.size > ZIP_LIMITS.zipBytes) {
    preview.errors.push(`ZIP 超过 ${formatMB(ZIP_LIMITS.zipBytes)} 上限`);
    return preview;
  }

  const members = await listZipMembers(file);

  let totalBytes = 0;
  let pdfCount = 0;
  const seen = new Set<string>();
  for (const member of members) {
    let basename: string | null;
    try {
      basename = flattenMemberName(member.name);
    } catch (error) {
      preview.errors.push((error as Error).message);
      continue;
    }
    if (basename === null) {
      preview.skipped.push({ name: member.name, reason: 'junk' });
      continue;
    }
    const key = basename.toLowerCase();
    if (seen.has(key)) {
      preview.errors.push(`ZIP 内文件名扁平化后重复:${basename}`);
      continue;
    }
    seen.add(key);
    totalBytes += member.size;
    const dot = basename.lastIndexOf('.');
    const suffix = dot === -1 ? '' : basename.slice(dot).toLowerCase();
    if (suffix === '.pdf') {
      pdfCount += 1;
      if (member.size > ZIP_LIMITS.reportBytes) {
        preview.errors.push(`报告 PDF ${basename} 超过 ${formatMB(ZIP_LIMITS.reportBytes)} 限制`);
      } else {
        preview.report = { name: basename, size: member.size };
      }
    } else if ((CODE_EXTENSIONS as readonly string[]).includes(suffix)) {
      const questionNumber = zipQuestionNumber(basename);
      if (questionNumber === null) {
        preview.errors.push(`代码文件 ${basename} 的文件名需以小题号数字结尾(如 task3.py / q3.py / CW1_3.py)`);
        continue;
      }
      if (member.size > ZIP_LIMITS.codeFileBytes) {
        preview.errors.push(`代码文件 ${basename} 超过单文件大小限制`);
        continue;
      }
      preview.code.push({
        name: basename,
        size: member.size,
        questionNumber,
      });
    } else if ((FORBIDDEN_INPUT_EXTENSIONS as readonly string[]).includes(suffix)) {
      preview.errors.push(`数据集不能是源码、可执行或归档文件:${basename}`);
    } else if (member.size > ZIP_LIMITS.datasetFileBytes) {
      preview.errors.push(`数据集 ${basename} 超过单文件大小限制`);
    } else {
      preview.datasets.push({ name: basename, size: member.size });
    }
  }

  if (pdfCount === 0) preview.errors.push('ZIP 中缺少报告 PDF');
  if (pdfCount > 1) preview.errors.push('ZIP 中只能包含一份报告 PDF');
  if (members.length > ZIP_LIMITS.members) {
    preview.errors.push(`ZIP 成员最多 ${ZIP_LIMITS.members} 个`);
  }
  if (totalBytes > ZIP_LIMITS.uncompressedBytes) {
    preview.errors.push(`ZIP 解压后总大小超过 ${formatMB(ZIP_LIMITS.uncompressedBytes)} 限制`);
  }
  if (preview.code.length > ZIP_LIMITS.codeFiles) {
    preview.errors.push(`代码文件最多 ${ZIP_LIMITS.codeFiles} 个`);
  }
  if (preview.datasets.length > ZIP_LIMITS.datasetFiles) {
    preview.errors.push(`数据集文件最多 ${ZIP_LIMITS.datasetFiles} 个`);
  }
  const datasetTotal = preview.datasets.reduce((sum, d) => sum + d.size, 0);
  if (datasetTotal > ZIP_LIMITS.datasetTotalBytes) {
    preview.errors.push(`数据集累计超过 ${formatMB(ZIP_LIMITS.datasetTotalBytes)} 限制`);
  }
  return preview;
}

function formatMB(bytes: number): string {
  return `${Math.round(bytes / (1024 * 1024))}MB`;
}

interface ZipMemberInfo {
  name: string;
  size: number;
}

/** Blob.arrayBuffer 兼容读取(jsdom 测试环境/老浏览器无此方法)。 */
function readSlice(blob: Blob): Promise<Uint8Array> {
  if (typeof blob.arrayBuffer === 'function') {
    return blob.arrayBuffer().then((buffer) => new Uint8Array(buffer));
  }
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(new Uint8Array(reader.result as ArrayBuffer));
    reader.onerror = () => reject(reader.error ?? new Error('读取文件失败'));
    reader.readAsArrayBuffer(blob);
  });
}

/** 解析 End of Central Directory + 中央目录条目(文件名与原始大小)。 */
async function listZipMembers(file: File): Promise<ZipMemberInfo[]> {
  if (file.size < 22) {
    throw new Error('文件内容不是有效 ZIP');
  }
  const tailSize = Math.min(file.size, 64 * 1024);
  const tail = await readSlice(file.slice(file.size - tailSize));
  const eocd = findEocd(tail);
  if (eocd === null) {
    throw new Error('文件内容不是有效 ZIP');
  }
  const view = new DataView(tail.buffer, tail.byteOffset, tail.byteLength);
  const entryCount = view.getUint16(eocd + 10, true);
  const cdSize = view.getUint32(eocd + 12, true);
  const cdOffset = view.getUint32(eocd + 16, true);
  if (cdOffset + cdSize > file.size) {
    throw new Error('文件内容不是有效 ZIP');
  }
  const cd = await readSlice(file.slice(cdOffset, cdOffset + cdSize));
  const cdView = new DataView(cd.buffer, cd.byteOffset, cd.byteLength);
  const decoder = new TextDecoder();
  const members: ZipMemberInfo[] = [];
  let cursor = 0;
  for (let i = 0; i < entryCount && cursor + 46 <= cd.length; i += 1) {
    const signature = cdView.getUint32(cursor, true);
    if (signature !== 0x02014b50) break; // central directory file header
    const flags = cdView.getUint16(cursor + 8, true);
    const nameLength = cdView.getUint16(cursor + 28, true);
    const extraLength = cdView.getUint16(cursor + 30, true);
    const commentLength = cdView.getUint16(cursor + 32, true);
    const uncompressedSize = cdView.getUint32(cursor + 24, true);
    const name = decoder.decode(cd.subarray(cursor + 46, cursor + 46 + nameLength));
    if ((flags & 0x1) !== 0) {
      throw new Error(`ZIP 成员已加密,请去除密码后重新打包:${name}`);
    }
    members.push({ name, size: uncompressedSize });
    cursor += 46 + nameLength + extraLength + commentLength;
  }
  if (members.length === 0) {
    throw new Error('文件内容不是有效 ZIP');
  }
  return members;
}

function findEocd(tail: Uint8Array): number | null {
  const lastScan = Math.max(0, tail.length - 22 - 0xffff);
  for (let i = tail.length - 22; i >= lastScan; i -= 1) {
    if (
      tail[i] === 0x50 && tail[i + 1] === 0x4b &&
      tail[i + 2] === 0x05 && tail[i + 3] === 0x06
    ) {
      return i;
    }
  }
  return null;
}
