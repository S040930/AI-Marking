import { describe, expect, it } from 'vitest';

import {
  analyzeZip,
  sniffUploadKind,
  ZIP_LIMITS,
} from '@/lib/zipPreview';

/** 构造一个真实 ZIP(本地文件头 + 中央目录),供 analyzeZip 解析。 */
async function zipFile(entries: Record<string, string | Uint8Array>): Promise<File> {
  const chunks: { header: Uint8Array; nameBytes: Uint8Array; data: Uint8Array }[] = [];
  const central: Uint8Array[] = [];
  const encoder = new TextEncoder();
  let offset = 0;
  const crcTable = (() => {
    const table = new Uint32Array(256);
    for (let n = 0; n < 256; n += 1) {
      let c = n;
      for (let k = 0; k < 8; k += 1) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
      table[n] = c >>> 0;
    }
    return table;
  })();
  const crc32 = (data: Uint8Array) => {
    let c = 0xffffffff;
    for (const byte of data) c = crcTable[(c ^ byte) & 0xff] ^ (c >>> 8);
    return (c ^ 0xffffffff) >>> 0;
  };
  const u16 = (v: number) => new Uint8Array([v & 0xff, (v >> 8) & 0xff]);
  const u32 = (v: number) =>
    new Uint8Array([v & 0xff, (v >> 8) & 0xff, (v >> 16) & 0xff, (v >>> 24) & 0xff]);

  for (const [name, raw] of Object.entries(entries)) {
    const nameBytes = encoder.encode(name);
    const data = typeof raw === 'string' ? encoder.encode(raw) : raw;
    const header = new Uint8Array([
      ...u32(0x04034b50), ...u16(20), ...u16(0), ...u16(0), ...u16(0), ...u16(0),
      ...u32(crc32(data)), ...u32(data.length), ...u32(data.length),
      ...u16(nameBytes.length), ...u16(0),
    ]);
    chunks.push({ header, nameBytes, data });
    const entry = new Uint8Array([
      ...u32(0x02014b50), ...u16(20), ...u16(20), ...u16(0), ...u16(0), ...u16(0),
      ...u16(0), ...u32(crc32(data)), ...u32(data.length), ...u32(data.length),
      ...u16(nameBytes.length), ...u16(0), ...u16(0), ...u16(0), ...u16(0), ...u32(0),
      ...u32(offset),
    ]);
    central.push(entry, nameBytes);
    offset += header.length + nameBytes.length + data.length;
  }
  const cdStart = offset;
  const cdBytes = central.reduce((acc, part) => {
    const next = new Uint8Array(acc.length + part.length);
    next.set(acc);
    next.set(part, acc.length);
    return next;
  }, new Uint8Array());
  const eocd = new Uint8Array([
    ...u32(0x06054b50), ...u16(0), ...u16(0),
    ...u16(Object.keys(entries).length), ...u16(Object.keys(entries).length),
    ...u32(cdBytes.length), ...u32(cdStart), ...u16(0),
  ]);
  const local = chunks.reduce((acc, part) => {
    const next = new Uint8Array(acc.length + part.header.length + part.nameBytes.length + part.data.length);
    next.set(acc);
    next.set(part.header, acc.length);
    next.set(part.nameBytes, acc.length + part.header.length);
    next.set(part.data, acc.length + part.header.length + part.nameBytes.length);
    return next;
  }, new Uint8Array());
  const all = new Uint8Array(local.length + cdBytes.length + eocd.length);
  all.set(local);
  all.set(cdBytes, local.length);
  all.set(eocd, local.length + cdBytes.length);
  return new File([all], 'assignment.zip', { type: 'application/zip' });
}

describe('sniffUploadKind', () => {
  it('识别 pdf/zip/其他', () => {
    expect(sniffUploadKind('a.pdf')).toBe('pdf');
    expect(sniffUploadKind('a.PDF')).toBe('pdf');
    expect(sniffUploadKind('a.zip')).toBe('zip');
    expect(sniffUploadKind('a.docx')).toBeNull();
  });
});

describe('analyzeZip', () => {
  it('happy path:1 PDF + q1.py + csv → 分类正确且无错误', async () => {
    const file = await zipFile({
      '报告.pdf': '%PDF-1.4',
      'task1/q1.py': 'print(1)',
      'data/battery.csv': 'a,b\n1,2\n',
    });
    const preview = await analyzeZip(file);
    expect(preview.errors).toEqual([]);
    expect(preview.report?.name).toBe('报告.pdf');
    expect(preview.code).toEqual([
      { name: 'q1.py', size: 8, questionNumber: 1 },
    ]);
    expect(preview.datasets.map((d) => d.name)).toEqual(['battery.csv']);
  });

  it('缺少报告 PDF → errors 含提示', async () => {
    const file = await zipFile({ 'q1.py': 'print(1)' });
    const preview = await analyzeZip(file);
    expect(preview.errors).toContain('ZIP 中缺少报告 PDF');
  });

  it('两份 PDF → errors 含提示', async () => {
    const file = await zipFile({ 'a.pdf': '%PDF', 'b.pdf': '%PDF' });
    const preview = await analyzeZip(file);
    expect(preview.errors).toContain('ZIP 中只能包含一份报告 PDF');
  });

  it('非 q<n> 命名代码 → errors 含提示', async () => {
    const file = await zipFile({ '报告.pdf': '%PDF', 'main.py': 'print(1)' });
    const preview = await analyzeZip(file);
    expect(preview.errors.some((e) => e.includes('小题号'))).toBe(true);
  });

  it('taskN 等尾部数字命名推导小题号', async () => {
    const file = await zipFile({
      '报告.pdf': '%PDF',
      'task1.py': 'a=1',
      'task2.py': 'b=2',
      'task3.ipynb': '{"cells": []}',
      'data/d.csv': 'x\n',
    });
    const preview = await analyzeZip(file);
    expect(preview.errors).toEqual([]);
    expect(preview.code.map((c) => [c.name, c.questionNumber])).toEqual([
      ['task1.py', 1],
      ['task2.py', 2],
      ['task3.ipynb', 3],
    ]);
  });

  it('小题号取严格尾部数字组:CW1_3→3、q1_v2→2、task12→12、CW1_v2→2', async () => {
    const file = await zipFile({
      '报告.pdf': '%PDF',
      'CW1_3.py': 'a=1',
      'q1_v2.py': 'b=2',
      'task12.py': 'c=3',
      'CW1_v2.ipynb': '{"cells": []}',
    });
    const preview = await analyzeZip(file);
    expect(preview.errors).toEqual([]);
    expect(new Set(preview.code.map((c) => [c.name, c.questionNumber]))).toEqual(
      new Set([
        ['CW1_3.py', 3],
        ['q1_v2.py', 2],
        ['task12.py', 12],
        ['CW1_v2.ipynb', 2],
      ]),
    );
  });

  it('路径穿越/绝对路径/隐藏文件 → errors', async () => {
    const traversal = await zipFile({ '报告.pdf': '%PDF', '../evil.py': 'x' });
    expect((await analyzeZip(traversal)).errors.some((e) => e.includes('路径穿越'))).toBe(true);
    const absolute = await zipFile({ '报告.pdf': '%PDF', '/etc/x.py': 'x' });
    expect((await analyzeZip(absolute)).errors.some((e) => e.includes('绝对路径'))).toBe(true);
    const hidden = await zipFile({ '报告.pdf': '%PDF', '.hidden.py': 'x' });
    expect((await analyzeZip(hidden)).errors.some((e) => e.includes('隐藏文件'))).toBe(true);
  });

  it('__MACOSX 与 .DS_Store 被跳过', async () => {
    const file = await zipFile({
      '报告.pdf': '%PDF',
      '__MACOSX/._q1.py': 'junk',
      '.DS_Store': 'junk',
      'q1.py': 'print(1)',
    });
    const preview = await analyzeZip(file);
    expect(preview.errors).toEqual([]);
    expect(preview.skipped.map((s) => s.name)).toEqual(
      expect.arrayContaining(['__MACOSX/._q1.py', '.DS_Store']),
    );
    expect(preview.code.map((c) => c.name)).toEqual(['q1.py']);
  });

  it('显式目录条目(如 CW1/)跳过,不当作空数据集', async () => {
    const file = await zipFile({
      'CW1/': '',
      'CW1/报告.pdf': '%PDF',
      'CW1/task1.py': 'print(1)',
      'CW1/data/battery.csv': 'a,b\n',
    });
    const preview = await analyzeZip(file);
    expect(preview.errors).toEqual([]);
    expect(preview.report?.name).toBe('报告.pdf');
    expect(preview.code.map((c) => c.name)).toEqual(['task1.py']);
    expect(preview.datasets.map((d) => d.name)).toEqual(['battery.csv']);
  });

  it('扁平化重名 → errors', async () => {
    const file = await zipFile({
      '报告.pdf': '%PDF',
      'a/q1.py': 'x=1',
      'b/q1.py': 'x=2',
    });
    const preview = await analyzeZip(file);
    expect(preview.errors.some((e) => e.includes('重复'))).toBe(true);
  });

  it('归档/可执行扩展名的数据集 → errors', async () => {
    const archive = await zipFile({ '报告.pdf': '%PDF', 'data/x.zip': 'PK' });
    expect((await analyzeZip(archive)).errors.some((e) => e.includes('归档'))).toBe(true);
    const elf = await zipFile({ '报告.pdf': '%PDF', 'tool.exe': 'MZ' });
    expect((await analyzeZip(elf)).errors.some((e) => e.includes('可执行'))).toBe(true);
  });

  it('成员数量/数据集数量超限 → errors', async () => {
    const entries: Record<string, string> = { '报告.pdf': '%PDF' };
    const manyDatasets = ZIP_LIMITS.datasetFiles + 1;
    for (let i = 0; i < manyDatasets; i += 1) entries[`d${i}.csv`] = '1';
    const preview = await analyzeZip(await zipFile(entries));
    expect(preview.errors.some((e) => e.includes('数据集文件最多'))).toBe(true);
  });

  it('非 ZIP 内容 → analyzeZip 抛错', async () => {
    const file = new File(['not a zip at all'], 'a.zip');
    await expect(analyzeZip(file)).rejects.toThrow('ZIP');
  });
});
