import { useMemo, useRef, useState, type DragEvent, type JSX } from 'react';
import { createProduct, startProductVideo } from '../api';

type VoiceType = 'voiceover' | 'in-character dialogue' | 'ASMR' | 'narrator + character';
type Language = 'hi' | 'en' | 'hi-en mix';
type ReviewMode = 'auto' | 'per_clip';

const FEELING_SUGGESTIONS = [
  'calm morning ritual',
  'quiet pride',
  'playful breakthrough',
  'unhurried craft',
  'shared warmth',
  'effortless flow',
];

const VISUAL_STYLE_OPTIONS = [
  'Photorealistic product film',
  'Cinematic documentary',
  'Hyperreal commercial',
  'Noir high contrast',
  'Hand-drawn animation',
  'Stop motion',
  'Anime painterly',
  'Pixar 3D character',
  'Auto (let the planner pick)',
] as const;

type VisualStyle = (typeof VISUAL_STYLE_OPTIONS)[number];

const MAX_IMAGES = 10;
const MAX_VIDEOS = 3;
const MAX_IMAGE_BYTES = 5 * 1024 * 1024;
const MAX_VIDEO_BYTES = 50 * 1024 * 1024;

interface UploadItem {
  id: string;
  file: File;
  previewUrl: string;
}

function makeItem(file: File): UploadItem {
  return {
    id: `${file.name}-${file.size}-${file.lastModified}-${Math.random().toString(36).slice(2, 8)}`,
    file,
    previewUrl: URL.createObjectURL(file),
  };
}

function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

interface FieldErrors {
  company_name?: string;
  product_name?: string;
  website_url?: string;
  total_duration_s?: string;
  clip_duration_s?: string;
  feeling_to_evoke?: string;
  images?: string;
  videos?: string;
}

export function ProductBriefForm(): JSX.Element {
  // Section 1
  const [companyName, setCompanyName] = useState('');
  const [productName, setProductName] = useState('');
  const [productDescription, setProductDescription] = useState('');
  const [targetAudience, setTargetAudience] = useState('');
  const [websiteUrl, setWebsiteUrl] = useState('');

  // Section 2
  const [feelingToEvoke, setFeelingToEvoke] = useState('');
  const [visionStatement, setVisionStatement] = useState('');
  const [voiceTone, setVoiceTone] = useState('');
  const [voiceType, setVoiceType] = useState<VoiceType>('voiceover');
  const [language, setLanguage] = useState<Language>('hi-en mix');

  // Section 3
  const [totalDurationS, setTotalDurationS] = useState(50);
  const [clipDurationS, setClipDurationS] = useState(10);
  const [hookPrompt, setHookPrompt] = useState('');
  const [middlePrompt, setMiddlePrompt] = useState('');
  const [ctaPrompt, setCtaPrompt] = useState('');

  // Section 4
  const [visualStyle, setVisualStyle] = useState<VisualStyle>('Photorealistic product film');

  // Sections 5 & 6
  const [images, setImages] = useState<UploadItem[]>([]);
  const [videos, setVideos] = useState<UploadItem[]>([]);
  const [imgDragOver, setImgDragOver] = useState(false);
  const [vidDragOver, setVidDragOver] = useState(false);
  const imgInputRef = useRef<HTMLInputElement | null>(null);
  const vidInputRef = useRef<HTMLInputElement | null>(null);

  // Section 7
  const [reviewMode, setReviewMode] = useState<ReviewMode>('auto');

  // Submission
  const [submitting, setSubmitting] = useState(false);
  const [apiError, setApiError] = useState<string | null>(null);
  const [errors, setErrors] = useState<FieldErrors>({});

  const clipCount = useMemo(() => {
    if (clipDurationS <= 0) return 0;
    return Math.max(1, Math.floor(totalDurationS / clipDurationS));
  }, [totalDurationS, clipDurationS]);

  const requiredOk =
    companyName.trim() !== '' &&
    productName.trim() !== '' &&
    feelingToEvoke.trim() !== '' &&
    images.length >= 1;

  function validate(): FieldErrors {
    const e: FieldErrors = {};
    if (!companyName.trim()) e.company_name = 'Company name is required.';
    if (!productName.trim()) e.product_name = 'Product name is required.';
    if (!feelingToEvoke.trim()) e.feeling_to_evoke = 'A feeling to evoke is required.';
    if (websiteUrl.trim()) {
      try {
        const u = new URL(websiteUrl.trim());
        if (!/^https?:$/.test(u.protocol)) {
          e.website_url = 'URL must start with http:// or https://.';
        }
      } catch {
        e.website_url = 'Enter a valid URL.';
      }
    }
    if (totalDurationS < 20 || totalDurationS > 120) {
      e.total_duration_s = 'Total duration must be between 20 and 120 seconds.';
    }
    if (clipDurationS < 5 || clipDurationS > 15) {
      e.clip_duration_s = 'Clip duration must be between 5 and 15 seconds.';
    }
    if (images.length < 1) e.images = 'Add at least one product image.';
    return e;
  }

  function addImageFiles(files: FileList | File[]) {
    const incoming = Array.from(files);
    const errs: string[] = [];
    const accepted: UploadItem[] = [];
    for (const f of incoming) {
      if (!f.type.startsWith('image/')) {
        errs.push(`${f.name}: not an image.`);
        continue;
      }
      if (f.size > MAX_IMAGE_BYTES) {
        errs.push(`${f.name}: over 5 MB.`);
        continue;
      }
      if (images.length + accepted.length >= MAX_IMAGES) {
        errs.push(`Max ${MAX_IMAGES} images.`);
        break;
      }
      accepted.push(makeItem(f));
    }
    if (accepted.length) setImages(prev => [...prev, ...accepted]);
    setErrors(prev => ({ ...prev, images: errs.length ? errs.join(' ') : undefined }));
  }

  function addVideoFiles(files: FileList | File[]) {
    const incoming = Array.from(files);
    const errs: string[] = [];
    const accepted: UploadItem[] = [];
    for (const f of incoming) {
      if (!f.type.startsWith('video/')) {
        errs.push(`${f.name}: not a video.`);
        continue;
      }
      if (f.size > MAX_VIDEO_BYTES) {
        errs.push(`${f.name}: over 50 MB.`);
        continue;
      }
      if (videos.length + accepted.length >= MAX_VIDEOS) {
        errs.push(`Max ${MAX_VIDEOS} videos.`);
        break;
      }
      accepted.push(makeItem(f));
    }
    if (accepted.length) setVideos(prev => [...prev, ...accepted]);
    setErrors(prev => ({ ...prev, videos: errs.length ? errs.join(' ') : undefined }));
  }

  function removeImage(id: string) {
    setImages(prev => {
      const next = prev.filter(p => p.id !== id);
      const gone = prev.find(p => p.id === id);
      if (gone) URL.revokeObjectURL(gone.previewUrl);
      return next;
    });
  }

  function removeVideo(id: string) {
    setVideos(prev => {
      const next = prev.filter(p => p.id !== id);
      const gone = prev.find(p => p.id === id);
      if (gone) URL.revokeObjectURL(gone.previewUrl);
      return next;
    });
  }

  function onImageDrop(e: DragEvent<HTMLDivElement>) {
    e.preventDefault();
    setImgDragOver(false);
    if (e.dataTransfer.files?.length) addImageFiles(e.dataTransfer.files);
  }

  function onVideoDrop(e: DragEvent<HTMLDivElement>) {
    e.preventDefault();
    setVidDragOver(false);
    if (e.dataTransfer.files?.length) addVideoFiles(e.dataTransfer.files);
  }

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    const ve = validate();
    setErrors(ve);
    if (Object.keys(ve).length) return;

    setApiError(null);
    setSubmitting(true);
    try {
      const fd = new FormData();
      fd.append('company_name', companyName.trim());
      fd.append('product_name', productName.trim());
      fd.append('product_description', productDescription.trim());
      fd.append('target_audience', targetAudience.trim());
      fd.append('website_url', websiteUrl.trim());
      fd.append('feeling_to_evoke', feelingToEvoke.trim());
      if (visionStatement.trim()) {
        fd.append('vision_statement', visionStatement.trim());
      }
      if (visualStyle !== 'Auto (let the planner pick)') {
        fd.append('visual_style_preference', visualStyle);
      }
      fd.append('voice_tone', voiceTone.trim());
      fd.append('voice_type', voiceType);
      fd.append('language', language);
      fd.append('total_duration_s', String(totalDurationS));
      fd.append('clip_duration_s', String(clipDurationS));
      fd.append('clip_count', String(clipCount));
      fd.append('structure_hook_prompt', hookPrompt.trim());
      fd.append('structure_middle_prompt', middlePrompt.trim());
      fd.append('structure_cta_prompt', ctaPrompt.trim());
      fd.append('review_mode', reviewMode);
      for (const it of images) fd.append('product_images', it.file, it.file.name);
      for (const it of videos) fd.append('product_videos', it.file, it.file.name);

      const created = await createProduct(fd);
      await startProductVideo(created.run_id, {
        review_mode: reviewMode,
        clip_count: clipCount,
        clip_duration_s: clipDurationS,
        // Default to skipping the YouTube upload step during this testing
        // phase: the OAuth token expires and crashes the run. Flip this off
        // (or wire up a UI toggle) once the upload step is back to green.
        skip_upload: true,
      });
      window.location.hash = '#/run/' + created.run_id;
    } catch (err: any) {
      setApiError(err?.message || String(err));
    } finally {
      setSubmitting(false);
    }
  }

  const inputCls =
    'w-full px-3 py-2 bg-zinc-900 border border-zinc-700 rounded-md ' +
    'focus:outline-none focus:border-emerald-500 focus:ring-1 focus:ring-emerald-500/40 ' +
    'placeholder:text-zinc-600 transition';

  const labelCls = 'block text-sm font-medium mb-1.5 text-zinc-300';
  const subLabelCls = 'text-zinc-500 font-normal';
  const errCls = 'mt-1 text-xs text-red-400';
  const sectionCls = 'rounded-lg border border-zinc-800 bg-zinc-900/30 p-4 space-y-4';
  const sectionHeadCls = 'flex items-center gap-2 mb-1';
  const sectionTitleCls = 'text-sm font-semibold text-zinc-200 uppercase tracking-wider';
  const sectionBadgeCls =
    'inline-flex items-center justify-center w-5 h-5 rounded-full ' +
    'bg-emerald-500/10 text-emerald-400 text-[10px] font-semibold';

  return (
    <div className="flex-1 flex items-start justify-center p-8 overflow-y-auto">
      <form onSubmit={onSubmit} className="w-full max-w-3xl space-y-6">
        {/* Heading */}
        <div>
          <div className="flex items-center gap-2 mb-1">
            <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full bg-emerald-500/10 border border-emerald-500/30 text-emerald-400 text-[11px] font-medium uppercase tracking-wider">
              <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse" />
              Product video
            </span>
            <h1 className="text-2xl font-semibold">Brief your product</h1>
          </div>
          <p className="text-sm text-zinc-500">
            We&apos;ll plan a {totalDurationS}s short as {clipCount} clip{clipCount === 1 ? '' : 's'} —
            Hook, Middle, CTA — then generate each clip with continuity from the last frame of the previous one.
          </p>
        </div>

        {/* Section 1: Company & product */}
        <div className={sectionCls}>
          <div className={sectionHeadCls}>
            <span className={sectionBadgeCls}>1</span>
            <h2 className={sectionTitleCls}>Company & product</h2>
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div>
              <label className={labelCls}>
                Company name <span className="text-red-400">*</span>
              </label>
              <input
                value={companyName}
                onChange={e => setCompanyName(e.target.value)}
                placeholder="Acme Inc."
                className={inputCls}
              />
              {errors.company_name && <div className={errCls}>{errors.company_name}</div>}
            </div>
            <div>
              <label className={labelCls}>
                Product name <span className="text-red-400">*</span>
              </label>
              <input
                value={productName}
                onChange={e => setProductName(e.target.value)}
                placeholder="Acme SmartBlender X1"
                className={inputCls}
              />
              {errors.product_name && <div className={errCls}>{errors.product_name}</div>}
            </div>
          </div>
          <div>
            <label className={labelCls}>
              Product description <span className={subLabelCls}>— what it is, what it does</span>
            </label>
            <textarea
              value={productDescription}
              onChange={e => setProductDescription(e.target.value)}
              rows={3}
              placeholder="A countertop blender that brews smoothies in 30 seconds with built-in scales."
              className={inputCls}
            />
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div>
              <label className={labelCls}>Target audience</label>
              <input
                value={targetAudience}
                onChange={e => setTargetAudience(e.target.value)}
                placeholder="Urban Gen-Z health enthusiasts in India"
                className={inputCls}
              />
            </div>
            <div>
              <label className={labelCls}>
                Website URL <span className={subLabelCls}>— optional, we&apos;ll scrape it</span>
              </label>
              <input
                type="url"
                value={websiteUrl}
                onChange={e => setWebsiteUrl(e.target.value)}
                placeholder="https://acme.com/smartblender"
                className={inputCls}
              />
              {errors.website_url && <div className={errCls}>{errors.website_url}</div>}
            </div>
          </div>
        </div>

        {/* Section 2: Feeling & voice */}
        <div className={sectionCls}>
          <div className={sectionHeadCls}>
            <span className={sectionBadgeCls}>2</span>
            <h2 className={sectionTitleCls}>Feeling & voice</h2>
          </div>
          <div>
            <label className={labelCls}>
              Feeling to evoke <span className="text-red-400">*</span>{' '}
              <span className={subLabelCls}>— what should the viewer feel?</span>
            </label>
            <input
              value={feelingToEvoke}
              onChange={e => setFeelingToEvoke(e.target.value)}
              placeholder="calm morning ritual"
              list="feeling-suggestions"
              className={inputCls}
            />
            <datalist id="feeling-suggestions">
              {FEELING_SUGGESTIONS.map(f => <option key={f} value={f} />)}
            </datalist>
            {errors.feeling_to_evoke && <div className={errCls}>{errors.feeling_to_evoke}</div>}
          </div>
          <div>
            <label className={labelCls}>
              Vision statement <span className={subLabelCls}>— optional, the why behind the brand</span>
            </label>
            <textarea
              value={visionStatement}
              onChange={e => setVisionStatement(e.target.value)}
              rows={2}
              placeholder="We believe that morning rituals deserve craft."
              className={inputCls}
            />
          </div>
          <p className="text-[11px] text-zinc-500 -mt-2">
            We use these to evoke a feeling and serve your vision — not to sell the product.
          </p>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div>
              <label className={labelCls}>
                Voice tone <span className={subLabelCls}>— how the speaker sounds</span>
              </label>
              <input
                value={voiceTone}
                onChange={e => setVoiceTone(e.target.value)}
                placeholder="warm, confident"
                className={inputCls}
              />
            </div>
            <div>
              <label className={labelCls}>Voice type</label>
              <select
                value={voiceType}
                onChange={e => setVoiceType(e.target.value as VoiceType)}
                className={inputCls}
              >
                <option value="voiceover">Voiceover</option>
                <option value="in-character dialogue">In-character dialogue</option>
                <option value="ASMR">ASMR</option>
                <option value="narrator + character">Narrator + character</option>
              </select>
            </div>
            <div>
              <label className={labelCls}>Language</label>
              <select
                value={language}
                onChange={e => setLanguage(e.target.value as Language)}
                className={inputCls}
              >
                <option value="hi">Hindi</option>
                <option value="en">English</option>
                <option value="hi-en mix">Hindi-English mix</option>
              </select>
            </div>
          </div>
        </div>

        {/* Section 3: Duration & structure */}
        <div className={sectionCls}>
          <div className={sectionHeadCls}>
            <span className={sectionBadgeCls}>3</span>
            <h2 className={sectionTitleCls}>Duration & structure</h2>
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
            <div>
              <label className={labelCls}>
                Total duration <span className={subLabelCls}>(20-120s)</span>
              </label>
              <div className="relative">
                <input
                  type="number"
                  min={20}
                  max={120}
                  value={totalDurationS}
                  onChange={e => setTotalDurationS(parseInt(e.target.value || '0', 10))}
                  className={inputCls + ' pr-8'}
                />
                <span className="absolute right-3 top-1/2 -translate-y-1/2 text-xs text-zinc-500">s</span>
              </div>
              {errors.total_duration_s && <div className={errCls}>{errors.total_duration_s}</div>}
            </div>
            <div>
              <label className={labelCls}>
                Clip duration <span className={subLabelCls}>(5-15s)</span>
              </label>
              <div className="relative">
                <input
                  type="number"
                  min={5}
                  max={15}
                  value={clipDurationS}
                  onChange={e => setClipDurationS(parseInt(e.target.value || '0', 10))}
                  className={inputCls + ' pr-8'}
                />
                <span className="absolute right-3 top-1/2 -translate-y-1/2 text-xs text-zinc-500">s</span>
              </div>
              {errors.clip_duration_s && <div className={errCls}>{errors.clip_duration_s}</div>}
            </div>
            <div>
              <label className={labelCls}>Resulting clips</label>
              <div className="flex items-center gap-2 px-3 py-2 bg-zinc-900/60 border border-zinc-800 rounded-md">
                <span className="text-2xl font-semibold text-emerald-400">{clipCount}</span>
                <span className="text-xs text-zinc-500">
                  Hook → Middle{clipCount > 2 ? ` ×${clipCount - 2}` : ''} → CTA
                </span>
              </div>
            </div>
          </div>
          <div>
            <label className={labelCls}>
              Hook prompt <span className={subLabelCls}>— guidance for clip 1 (first impression)</span>
            </label>
            <textarea
              value={hookPrompt}
              onChange={e => setHookPrompt(e.target.value)}
              rows={2}
              placeholder="Open with the feeling. What single image makes the viewer feel it in 2 seconds?"
              className={inputCls}
            />
          </div>
          <div>
            <label className={labelCls}>
              Middle prompt <span className={subLabelCls}>— guidance for middle clips (features/story)</span>
            </label>
            <textarea
              value={middlePrompt}
              onChange={e => setMiddlePrompt(e.target.value)}
              rows={2}
              placeholder="Show that feeling LIVED. What ritual or moment captures it?"
              className={inputCls}
            />
          </div>
          <div>
            <label className={labelCls}>
              CTA prompt <span className={subLabelCls}>— guidance for the final clip (call to action)</span>
            </label>
            <textarea
              value={ctaPrompt}
              onChange={e => setCtaPrompt(e.target.value)}
              rows={2}
              placeholder="Quiet invitation, not a hard sell. Step into the vision."
              className={inputCls}
            />
          </div>
        </div>

        {/* Section 4: Visual style */}
        <div className={sectionCls}>
          <div className={sectionHeadCls}>
            <span className={sectionBadgeCls}>4</span>
            <h2 className={sectionTitleCls}>Visual style</h2>
          </div>
          <div>
            <label className={labelCls}>
              Visual style preference <span className={subLabelCls}>— how should the film look?</span>
            </label>
            <select
              value={visualStyle}
              onChange={e => setVisualStyle(e.target.value as VisualStyle)}
              className={inputCls}
            >
              {VISUAL_STYLE_OPTIONS.map(s => <option key={s} value={s}>{s}</option>)}
            </select>
            <p className="text-[11px] text-zinc-500 mt-1.5">
              We default to photorealistic. Pick another only if you want a stylised look.
            </p>
          </div>
        </div>

        {/* Section 5: Product images */}
        <div className={sectionCls}>
          <div className={sectionHeadCls}>
            <span className={sectionBadgeCls}>5</span>
            <h2 className={sectionTitleCls}>
              Product images <span className="text-red-400 normal-case">*</span>
            </h2>
            <span className="text-xs text-zinc-500">— up to {MAX_IMAGES}, 5 MB each</span>
          </div>
          <div
            onDragOver={e => { e.preventDefault(); setImgDragOver(true); }}
            onDragLeave={() => setImgDragOver(false)}
            onDrop={onImageDrop}
            onClick={() => imgInputRef.current?.click()}
            className={
              'cursor-pointer rounded-md border-2 border-dashed p-6 text-center transition ' +
              (imgDragOver
                ? 'border-emerald-500/60 bg-emerald-500/5'
                : 'border-zinc-700 bg-zinc-900/40 hover:border-zinc-600')
            }
          >
            <div className="text-3xl mb-1 text-zinc-500">⬆</div>
            <div className="text-sm text-zinc-300">
              Drop images here or <span className="text-emerald-400">click to browse</span>
            </div>
            <div className="text-[11px] text-zinc-500 mt-1">
              PNG, JPG, WebP. Used as visual reference for clip 1.
            </div>
            <input
              ref={imgInputRef}
              type="file"
              accept="image/*"
              multiple
              hidden
              onChange={e => {
                if (e.target.files) addImageFiles(e.target.files);
                e.target.value = '';
              }}
            />
          </div>
          {errors.images && <div className={errCls}>{errors.images}</div>}
          {images.length > 0 && (
            <div className="grid grid-cols-3 sm:grid-cols-5 gap-2">
              {images.map(it => (
                <div
                  key={it.id}
                  className="relative group aspect-square rounded-md overflow-hidden border border-zinc-800 bg-zinc-900"
                >
                  <img src={it.previewUrl} alt={it.file.name} className="w-full h-full object-cover" />
                  <button
                    type="button"
                    onClick={(e) => { e.stopPropagation(); removeImage(it.id); }}
                    className="absolute top-1 right-1 w-6 h-6 rounded-full bg-zinc-950/80 text-zinc-200 text-xs
                               opacity-0 group-hover:opacity-100 hover:bg-red-500/80 hover:text-white transition"
                    aria-label="Remove image"
                  >
                    ✕
                  </button>
                  <div className="absolute bottom-0 inset-x-0 px-1.5 py-0.5 bg-zinc-950/80 text-[10px] text-zinc-400 truncate">
                    {formatBytes(it.file.size)}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Section 6: Product videos */}
        <div className={sectionCls}>
          <div className={sectionHeadCls}>
            <span className={sectionBadgeCls}>6</span>
            <h2 className={sectionTitleCls}>Product videos</h2>
            <span className="text-xs text-zinc-500">— optional, up to {MAX_VIDEOS}, 50 MB each</span>
          </div>
          <div
            onDragOver={e => { e.preventDefault(); setVidDragOver(true); }}
            onDragLeave={() => setVidDragOver(false)}
            onDrop={onVideoDrop}
            onClick={() => vidInputRef.current?.click()}
            className={
              'cursor-pointer rounded-md border-2 border-dashed p-6 text-center transition ' +
              (vidDragOver
                ? 'border-emerald-500/60 bg-emerald-500/5'
                : 'border-zinc-700 bg-zinc-900/40 hover:border-zinc-600')
            }
          >
            <div className="text-3xl mb-1 text-zinc-500">▶</div>
            <div className="text-sm text-zinc-300">
              Drop videos here or <span className="text-emerald-400">click to browse</span>
            </div>
            <div className="text-[11px] text-zinc-500 mt-1">
              MP4, MOV, WebM. Used as motion reference.
            </div>
            <input
              ref={vidInputRef}
              type="file"
              accept="video/*"
              multiple
              hidden
              onChange={e => {
                if (e.target.files) addVideoFiles(e.target.files);
                e.target.value = '';
              }}
            />
          </div>
          {errors.videos && <div className={errCls}>{errors.videos}</div>}
          {videos.length > 0 && (
            <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
              {videos.map(it => (
                <div
                  key={it.id}
                  className="relative group aspect-video rounded-md overflow-hidden border border-zinc-800 bg-zinc-900"
                >
                  <video src={it.previewUrl} muted playsInline className="w-full h-full object-cover" />
                  <button
                    type="button"
                    onClick={(e) => { e.stopPropagation(); removeVideo(it.id); }}
                    className="absolute top-1 right-1 w-6 h-6 rounded-full bg-zinc-950/80 text-zinc-200 text-xs
                               opacity-0 group-hover:opacity-100 hover:bg-red-500/80 hover:text-white transition"
                    aria-label="Remove video"
                  >
                    ✕
                  </button>
                  <div className="absolute bottom-0 inset-x-0 px-1.5 py-0.5 bg-zinc-950/80 text-[10px] text-zinc-400 truncate">
                    {it.file.name} · {formatBytes(it.file.size)}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Section 7: Generation mode */}
        <div className={sectionCls}>
          <div className={sectionHeadCls}>
            <span className={sectionBadgeCls}>7</span>
            <h2 className={sectionTitleCls}>Generation mode</h2>
          </div>
          <div className="inline-flex rounded-md border border-zinc-700 bg-zinc-900 p-0.5">
            <button
              type="button"
              onClick={() => setReviewMode('auto')}
              className={
                'px-4 py-1.5 rounded text-sm font-medium transition ' +
                (reviewMode === 'auto'
                  ? 'bg-emerald-500/15 text-emerald-300 ring-1 ring-emerald-500/50'
                  : 'text-zinc-400 hover:text-zinc-200')
              }
            >
              Fully automatic
            </button>
            <button
              type="button"
              onClick={() => setReviewMode('per_clip')}
              className={
                'px-4 py-1.5 rounded text-sm font-medium transition ' +
                (reviewMode === 'per_clip'
                  ? 'bg-emerald-500/15 text-emerald-300 ring-1 ring-emerald-500/50'
                  : 'text-zinc-400 hover:text-zinc-200')
              }
            >
              Review after each clip
            </button>
          </div>
          <p className="text-xs text-zinc-500">
            {reviewMode === 'auto'
              ? 'Runs the entire pipeline end-to-end without pausing — best for trusted briefs.'
              : 'Pauses after each clip so you can approve, edit the brief, or regenerate before continuing.'}
          </p>
        </div>

        {/* API error banner */}
        {apiError && (
          <div className="text-sm text-red-300 bg-red-500/10 border border-red-500/30 rounded p-3">
            <div className="font-medium mb-0.5">Couldn&apos;t start the run</div>
            <div className="text-xs text-red-300/80 break-words">{apiError}</div>
          </div>
        )}

        {/* Submit */}
        <div className="flex items-center gap-3 pt-1">
          <button
            type="submit"
            disabled={submitting || !requiredOk}
            className="group relative px-6 py-3 rounded-md font-medium
                       bg-gradient-to-b from-emerald-400 to-emerald-500 text-zinc-950
                       shadow-lg shadow-emerald-500/20
                       ring-1 ring-emerald-400/50
                       hover:from-emerald-300 hover:to-emerald-400 hover:shadow-emerald-500/30
                       disabled:from-zinc-800 disabled:to-zinc-800 disabled:text-zinc-500
                       disabled:ring-zinc-700 disabled:shadow-none disabled:cursor-not-allowed
                       transition-all"
          >
            <span className="inline-flex items-center gap-2">
              {submitting ? (
                <>
                  <span className="inline-block w-3 h-3 rounded-full border-2 border-zinc-950/40 border-t-zinc-950 animate-spin" />
                  Starting…
                </>
              ) : (
                <>
                  <span>▶</span>
                  Plan and generate
                </>
              )}
            </span>
          </button>
          {!requiredOk && !submitting && (
            <span className="text-xs text-zinc-500">
              Fill in company, product, feeling to evoke, and at least 1 image.
            </span>
          )}
        </div>
      </form>
    </div>
  );
}
