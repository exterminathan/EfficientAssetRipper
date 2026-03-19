using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Text.Json;
using System.Text.Json.Serialization;
using System.Threading;
using System.Threading.Tasks;
using System.IO.Compression;
using System.Net.Http;
using System.Runtime.InteropServices;
using CUE4Parse.Compression;
using CUE4Parse.Encryption.Aes;
using CUE4Parse.FileProvider;
using CUE4Parse.FileProvider.Objects;
using CUE4Parse.MappingsProvider;
using CUE4Parse.UE4.Assets;
using CUE4Parse.UE4.Assets.Exports;
using CUE4Parse.UE4.Assets.Exports.Animation;
using CUE4Parse.UE4.Assets.Exports.Material;
using CUE4Parse.UE4.Assets.Exports.SkeletalMesh;
using CUE4Parse.UE4.Assets.Exports.Sound;
using CUE4Parse.UE4.Assets.Exports.StaticMesh;
using CUE4Parse.UE4.Assets.Exports.Texture;
using CUE4Parse.UE4.Objects.Core.Misc;
using CUE4Parse.UE4.Versions;
using CUE4Parse_Conversion;
using CUE4Parse_Conversion.Animations;
using CUE4Parse_Conversion.Meshes;
using CUE4Parse_Conversion.Sounds;
using CUE4Parse_Conversion.Textures;
using CUE4Parse.UE4.Readers;
using CUE4Parse.UE4.Wwise;
using CUE4Parse.UE4.Wwise.Objects;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using Newtonsoft.Json.Serialization;

namespace CUE4ParseCLI;

internal static class Program
{
    private static DefaultFileProvider? _provider;
    private static CancellationTokenSource _cts = new();
    private static string _textureFormat = "png"; // png or tga
    private static string _audioFormat = "wav";   // wav or ogg
    private static string _gameDir = "";          // current game directory (for loose file reads)
    private static Dictionary<string, string> _looseFiles = new(StringComparer.OrdinalIgnoreCase); // VFS path → disk path for files CUE4Parse doesn't discover
    private static string? _vgmstreamPath;         // cached path to vgmstream-cli.exe
    private static Dictionary<string, string> _wemIdToName = new(StringComparer.OrdinalIgnoreCase); // WEM numeric ID → debug name
    private static string? _wemNameCachePath;       // per-game disk cache for WEM name map
    private static bool _wemMapBuilt;               // true once the map has been built/loaded this session
    private static Task? _exportTask;               // currently running export (if any)
    private static Task<string?>? _pendingReadTask;   // parked stdin read from RunExportWithCancelSupport
    private static JObject? _pendingGetProps;          // queued get_props command during export
    private static readonly object _respondLock = new(); // guards stdout writes during concurrent export

    private static readonly JsonSerializerOptions _writeOpts = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower,
        DefaultIgnoreCondition = JsonIgnoreCondition.WhenWritingNull
    };

    // ─── Entry ────────────────────────────────────────────────────────
    private static async Task Main(string[] args)
    {
        // If --version flag, print and exit
        if (args.Length > 0 && args[0] == "--version")
        {
            Console.WriteLine("CUE4ParseCLI 1.0.0");
            return;
        }

        // NDJSON stdin loop
        using var reader = new StreamReader(Console.OpenStandardInput());
        while (true)
        {
            // Consume any parked read left by RunExportWithCancelSupport
            string? line;
            if (_pendingReadTask != null)
            {
                line = await _pendingReadTask;
                _pendingReadTask = null;
            }
            else
            {
                line = await reader.ReadLineAsync();
            }
            if (line == null) break;
            if (string.IsNullOrWhiteSpace(line)) continue;
            try
            {
                var cmd = JObject.Parse(line);
                var cmdName = cmd.Value<string>("cmd") ?? "";
                switch (cmdName)
                {
                    case "init":
                        HandleInit(cmd);
                        break;
                    case "browse":
                        HandleBrowse(cmd);
                        break;
                    case "export":
                        await RunExportWithCancelSupport(reader, () => HandleExport(cmd));
                        break;
                    case "export_folder":
                        await RunExportWithCancelSupport(reader, () => HandleExportFolder(cmd));
                        break;
                    case "inspect":
                        HandleInspect(cmd);
                        break;
                    case "list_exports":
                        await RunExportWithCancelSupport(reader, () => HandleListExports(cmd));
                        break;
                    case "get_props":
                        HandleGetProps(cmd);
                        break;
                    case "rebuild_wem_cache":
                        HandleRebuildWemCache();
                        break;
                    case "scan_wwise_events":
                        await RunExportWithCancelSupport(reader, () => HandleScanWwiseEvents(cmd));
                        break;
                    case "export_wwise_audio":
                        await RunExportWithCancelSupport(reader, () => HandleExportWwiseAudio(cmd));
                        break;
                    case "cancel":
                        _cts.Cancel();
                        _cts = new CancellationTokenSource();
                        Respond(new { type = "cancelled" });
                        break;
                    case "quit":
                        Respond(new { type = "quit_ack" });
                        return;
                    default:
                        RespondError($"Unknown command: {cmdName}");
                        break;
                }
            }
            catch (Exception ex)
            {
                RespondError(ex.Message);
            }
        }
    }

    /// <summary>
    /// Run an export action on a background thread while continuing to read
    /// stdin for cancel/quit commands.  Browse commands are processed inline.
    /// Only ONE reader touches the StreamReader at a time (no concurrent reads).
    /// </summary>
    private static async Task RunExportWithCancelSupport(StreamReader reader, Action exportAction)
    {
        _exportTask = Task.Run(exportAction);

        // Single-threaded async read loop — avoids concurrent StreamReader access
        while (true)
        {
            var lineTask = _pendingReadTask ?? reader.ReadLineAsync();
            _pendingReadTask = null;

            var first = await Task.WhenAny(lineTask, _exportTask);
            if (first == _exportTask && !lineTask.IsCompleted)
            {
                // Export finished but a read is still pending — park it for the main loop
                _pendingReadTask = lineTask;
                break;
            }

            var cmdLine = await lineTask;
            if (cmdLine == null) break;
            if (string.IsNullOrWhiteSpace(cmdLine)) { if (_exportTask.IsCompleted) break; continue; }

            try
            {
                var subCmd = JObject.Parse(cmdLine);
                var subName = subCmd.Value<string>("cmd") ?? "";
                switch (subName)
                {
                    case "cancel":
                        _cts.Cancel();
                        Respond(new { type = "cancelled" });
                        break;
                    case "quit":
                        _cts.Cancel();
                        Respond(new { type = "quit_ack" });
                        await _exportTask;
                        _exportTask = null;
                        return;
                    case "browse":
                        // Browse is safe during export (read-only, no LoadPackage)
                        HandleBrowse(subCmd);
                        break;
                    case "get_props":
                        _pendingGetProps = subCmd; // queue for after export
                        break;
                    default:
                        break; // Other commands wait until export finishes
                }
            }
            catch { /* ignore malformed lines during export */ }

            if (_exportTask!.IsCompleted) break;
        }

        await _exportTask!;
        _exportTask = null;

        // Flush any queued get_props that arrived during export
        if (_pendingGetProps != null)
        {
            HandleGetProps(_pendingGetProps);
            _pendingGetProps = null;
        }

        // Reset the CTS for the next operation (in case cancel was used)
        if (_cts.IsCancellationRequested)
        {
            _cts.Dispose();
            _cts = new CancellationTokenSource();
        }
    }

    // ─── Init ─────────────────────────────────────────────────────────
    private static void HandleInit(JObject cmd)
    {
        var gameDir = cmd.Value<string>("game_dir") ?? throw new ArgumentException("game_dir required");
        var ueVersionStr = cmd.Value<string>("ue_version") ?? "GAME_UE5_4";
        var mappingsPath = cmd.Value<string>("mappings_path");

        // Parse UE version
        if (!Enum.TryParse<EGame>(ueVersionStr, true, out var ueVersion))
            ueVersion = EGame.GAME_UE5_4;

        // Ensure Oodle decompression DLL is available (required for IoStore .ucas/.utoc)
        InitOodle(gameDir);

        // Reset WEM name map — each new game directory gets a fresh map and its own disk cache
        _wemIdToName = new(StringComparer.OrdinalIgnoreCase);
        _wemMapBuilt = false;
        {
            // Build a stable per-directory cache key via FNV-1a over the lowercased path
            uint h = 2166136261u;
            foreach (var c in gameDir.ToLowerInvariant()) { h ^= c; h *= 16777619u; }
            _wemNameCachePath = Path.Combine(Path.GetTempPath(), "CUE4ParseCLI_wem_names", $"{h:x8}.json");
        }

        // Dispose old provider if re-initializing
        _provider?.Dispose();

#pragma warning disable CS0618
        _provider = new DefaultFileProvider(gameDir, SearchOption.AllDirectories,
            isCaseInsensitive: true,
            versions: new VersionContainer(ueVersion));
#pragma warning restore CS0618

        _provider.Initialize();

        // Register .upk files (UE3 packages) in the VFS — CUE4Parse auto-discovers
        // .uasset/.umap but not .upk, so we manually add them to enable LoadPackage()
        try
        {
            var upkDir = new DirectoryInfo(gameDir);
            if (upkDir.Exists)
            {
                // Files property returns FileProviderDictionary (extends Dictionary<string, GameFile>)
                // exposed as IReadOnlyDictionary — cast back to mutable to add .upk entries
                var filesDict = (IDictionary<string, GameFile>)_provider.Files;
                foreach (var f in upkDir.EnumerateFiles("*.upk", SearchOption.AllDirectories))
                {
                    var osFile = new OsGameFile(upkDir, f, "", _provider.Versions);
                    filesDict[osFile.Path] = osFile;
                }
            }
        }
        catch (Exception ex)
        {
            Respond(new { type = "warning", message = $"UPK registration: {ex.Message}" });
        }

        // Load mappings file if provided
        if (!string.IsNullOrEmpty(mappingsPath) && File.Exists(mappingsPath))
        {
            _provider.MappingsContainer = new FileUsmapTypeMappingsProvider(mappingsPath);
        }

        // Always submit a zero key for the default GUID to mount unencrypted archives
        // (CUE4Parse requires SubmitKey to actually mount .pak and IoStore .ucas/.utoc)
        var zeroKey = new FAesKey("0x0000000000000000000000000000000000000000000000000000000000000000");
        int keysSubmitted = 0;
        try
        {
            _provider.SubmitKey(new FGuid(), zeroKey);
            keysSubmitted++;
        }
        catch (Exception ex)
        {
            Respond(new { type = "warning", message = $"Zero key: {ex.Message}" });
        }

        // Submit user-provided AES keys for encrypted archives
        var keys = cmd["aes_keys"] as JArray ?? new JArray();
        foreach (var k in keys)
        {
            var guid = k.Value<string>("guid") ?? "00000000000000000000000000000000";
            var hex = k.Value<string>("key") ?? "";
            if (string.IsNullOrEmpty(hex)) continue;
            try
            {
                _provider.SubmitKey(new FGuid(guid), new FAesKey(hex));
                keysSubmitted++;
            }
            catch (Exception ex)
            {
                Respond(new { type = "warning", message = $"Key {guid}: {ex.Message}" });
            }
        }

        _provider.PostMount();

        var archiveCount = _provider.MountedVfs.Count;
        var fileCount = _provider.Files.Count;
        // Count archives still requiring keys
        int unmounted = 0;
        try { unmounted = _provider.RequiredKeys.Count(); } catch { }

        // Scan for loose files that CUE4Parse doesn't natively discover
        // (.upk, .wem, .ewem, .bnk — common in UE3 games like pre-F2P Rocket League)
        _gameDir = gameDir;
        _looseFiles.Clear();
        var looseExtensions = new HashSet<string>(StringComparer.OrdinalIgnoreCase)
            { ".upk", ".wem", ".ewem", ".bnk" };
        var gameDirInfo = new DirectoryInfo(gameDir);
        if (gameDirInfo.Exists)
        {
            try
            {
                foreach (var file in gameDirInfo.EnumerateFiles("*", SearchOption.AllDirectories))
                {
                    if (!looseExtensions.Contains(file.Extension)) continue;
                    var gamePath = Path.GetRelativePath(gameDir, file.FullName).Replace('\\', '/');
                    // Only add if CUE4Parse didn't already register it
                    if (!_provider.Files.ContainsKey(gamePath) &&
                        !_provider.Files.ContainsKey(gamePath.ToLowerInvariant()))
                    {
                        _looseFiles[gamePath] = file.FullName;
                    }
                }
            }
            catch (Exception ex)
            {
                Respond(new { type = "warning", message = $"Loose file scan: {ex.Message}" });
            }
        }

        Respond(new
        {
            type = "init_done",
            archive_count = archiveCount,
            unmounted_count = unmounted,
            file_count = fileCount + _looseFiles.Count,
            loose_file_count = _looseFiles.Count,
            keys_submitted = keysSubmitted
        });
    }

    // ─── Browse ───────────────────────────────────────────────────────
    private static void HandleBrowse(JObject cmd)
    {
        EnsureProvider();
        var path = (cmd.Value<string>("path") ?? "/").TrimEnd('/');
        if (string.IsNullOrEmpty(path)) path = "";

        // Normalize: remove leading slash for matching
        var prefix = path.Length > 0 ? path.TrimStart('/') + "/" : "";

        var entries = new List<object>();
        var seenFolders = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        var seenFiles = new HashSet<string>(StringComparer.OrdinalIgnoreCase);

        foreach (var file in _provider!.Files)
        {
            var gamePath = file.Value.Path;
            if (prefix.Length > 0 && !gamePath.StartsWith(prefix, StringComparison.OrdinalIgnoreCase))
                continue;

            var remainder = gamePath[prefix.Length..];
            var slashIdx = remainder.IndexOf('/');
            if (slashIdx >= 0)
            {
                var folderName = remainder[..slashIdx];
                if (seenFolders.Add(folderName))
                    entries.Add(new { name = folderName, is_folder = true });
            }
            else if (seenFiles.Add(remainder.ToLowerInvariant()))
            {
                var assetType = ClassifyAssetType(gamePath);
                entries.Add(new { name = remainder, is_folder = false, asset_type = assetType });
            }
        }

        // Include loose files not registered with CUE4Parse (.upk, .wem, .ewem, .bnk)
        foreach (var (loosePath, _) in _looseFiles)
        {
            if (prefix.Length > 0 && !loosePath.StartsWith(prefix, StringComparison.OrdinalIgnoreCase))
                continue;

            var remainder = loosePath[prefix.Length..];
            var slashIdx = remainder.IndexOf('/');
            if (slashIdx >= 0)
            {
                var folderName = remainder[..slashIdx];
                if (seenFolders.Add(folderName))
                    entries.Add(new { name = folderName, is_folder = true });
            }
            else if (seenFiles.Add(remainder.ToLowerInvariant()))
            {
                var assetType = ClassifyAssetType(loosePath);
                entries.Add(new { name = remainder, is_folder = false, asset_type = assetType });
            }
        }

        Respond(new
        {
            type = "browse_result",
            path,
            count = entries.Count,
            entries
        });
    }

    /// <summary>Classify a file's asset type using path heuristics only (no package loading).</summary>
    private static string ClassifyAssetType(string gamePath)
    {
        var lower = gamePath.ToLowerInvariant();
        // Non-package files are classified by extension
        if (lower.EndsWith(".wem")) return "Audio";
        if (lower.EndsWith(".ewem")) return "EncryptedAudio";
        if (lower.EndsWith(".bnk")) return "SoundBank";
        if (lower.EndsWith(".ubulk")) return "BulkData";
        if (lower.EndsWith(".umap")) return "Map";
        if (lower.EndsWith(".upk")) return "UE3Package";
        if (lower.EndsWith(".bin")) return "Binary";
        if (!lower.EndsWith(".uasset")) return "Unknown";

        // Path-based heuristic classification — safe, instant, no I/O
        if (lower.Contains("/wwiseaudio/"))
        {
            if (lower.Contains("/event/") || lower.Contains("/events/")) return "AkAudioEvent";
            if (lower.Contains("/media/")) return "AkMediaAsset";
            return "WwiseAsset";
        }

        var fileName = Path.GetFileNameWithoutExtension(lower);
        if (fileName.StartsWith("sm_")) return "StaticMesh";
        if (fileName.StartsWith("sk_") || fileName.StartsWith("skm_")) return "SkeletalMesh";
        if (fileName.StartsWith("t_")) return "Texture2D";
        if (fileName.StartsWith("m_")) return "Material";
        if (fileName.StartsWith("mi_")) return "MaterialInstance";
        if (fileName.StartsWith("abp_")) return "AnimBlueprint";
        if (fileName.StartsWith("bp_")) return "Blueprint";
        if (fileName.StartsWith("wbp_")) return "WidgetBlueprint";
        if (fileName.StartsWith("da_")) return "DataAsset";
        if (fileName.StartsWith("dt_")) return "DataTable";
        if (fileName.StartsWith("ns_")) return "NiagaraSystem";

        // Folder-path hints
        if (lower.Contains("/textures/") || lower.Contains("/texture/")) return "Texture2D";
        if (lower.Contains("/meshes/") || lower.Contains("/staticmesh")) return "StaticMesh";
        if (lower.Contains("/skeletal")) return "SkeletalMesh";
        if (lower.Contains("/animation") || lower.Contains("/anim/")) return "AnimSequence";
        if (lower.Contains("/sound/") || lower.Contains("/audio/")) return "SoundWave";
        if (lower.Contains("/material")) return "Material";
        if (lower.Contains("/niagara") || lower.Contains("/particle")) return "ParticleSystem";

        return "Asset";
    }

    // ─── Export single assets ─────────────────────────────────────────
    private static Task HandleExport(JObject cmd)
    {
        EnsureProvider();
        var paths = cmd["paths"]?.ToObject<List<string>>() ?? throw new ArgumentException("paths required");
        var outputDir = cmd.Value<string>("output_dir") ?? throw new ArgumentException("output_dir required");
        var formats = cmd["formats"]?.ToObject<Dictionary<string, bool>>() ?? new Dictionary<string, bool>
        {
            ["mesh"] = true,
            ["texture"] = true,
            ["props"] = true,
            ["animation"] = true,
            ["audio"] = true
        };
        _textureFormat = cmd.Value<string>("texture_format") ?? "png";
        _audioFormat = cmd.Value<string>("audio_format") ?? "wav";

        Directory.CreateDirectory(outputDir);

        int total = paths.Count, current = 0;
        var succeeded = new List<string>();
        var failed = new List<object>();

        foreach (var assetPath in paths)
        {
            if (_cts.Token.IsCancellationRequested) break;
            current++;
            Respond(new { type = "progress", current, total, message = $"Exporting: {Path.GetFileName(assetPath)}" });

            try
            {
                var exported = ExportAsset(assetPath, outputDir, formats);
                if (exported)
                    succeeded.Add(assetPath);
                else
                    failed.Add(new { path = assetPath, error = "Unsupported asset type or no exportable data" });
            }
            catch (Exception ex)
            {
                failed.Add(new { path = assetPath, error = ex.Message });
            }
        }

        Respond(new { type = "export_done", succeeded, failed, total = paths.Count });
        return Task.CompletedTask;
    }

    // ─── Export folder (bulk) ─────────────────────────────────────────
    private static Task HandleExportFolder(JObject cmd)
    {
        EnsureProvider();
        var vfsPath = (cmd.Value<string>("path") ?? "").TrimStart('/');
        var outputDir = cmd.Value<string>("output_dir") ?? throw new ArgumentException("output_dir required");
        var formats = cmd["formats"]?.ToObject<Dictionary<string, bool>>() ?? new Dictionary<string, bool>
        {
            ["mesh"] = true,
            ["texture"] = true,
            ["props"] = true,
            ["animation"] = true,
            ["audio"] = true
        };
        _textureFormat = cmd.Value<string>("texture_format") ?? "png";
        _audioFormat = cmd.Value<string>("audio_format") ?? "wav";

        Directory.CreateDirectory(outputDir);

        // Find all assets under this VFS path (including raw audio files)
        var prefix = string.IsNullOrEmpty(vfsPath) ? "" : vfsPath + "/";
        var exportableExtensions = new[] { ".uasset", ".umap", ".upk", ".wem", ".ewem", ".bnk" };
        var assetPaths = _provider!.Files
            .Where(f => prefix.Length == 0 || f.Value.Path.StartsWith(prefix, StringComparison.OrdinalIgnoreCase))
            .Select(f => f.Value.Path)
            .Where(p => exportableExtensions.Any(ext => p.EndsWith(ext, StringComparison.OrdinalIgnoreCase)))
            .ToList();

        // Also include loose files (not in provider)
        foreach (var (loosePath, _) in _looseFiles)
        {
            if (prefix.Length > 0 && !loosePath.StartsWith(prefix, StringComparison.OrdinalIgnoreCase))
                continue;
            if (exportableExtensions.Any(ext => loosePath.EndsWith(ext, StringComparison.OrdinalIgnoreCase)))
                assetPaths.Add(loosePath);
        }

        int total = assetPaths.Count, current = 0;
        var succeeded = new List<string>();
        var failed = new List<object>();

        Respond(new { type = "progress", current = 0, total, message = $"Found {total} assets to export" });

        foreach (var assetPath in assetPaths)
        {
            if (_cts.Token.IsCancellationRequested) break;
            current++;

            if (current % 10 == 0 || current == total)
                Respond(new { type = "progress", current, total, message = $"Exporting: {Path.GetFileName(assetPath)}" });

            try
            {
                var exported = ExportAsset(assetPath, outputDir, formats);
                if (exported)
                    succeeded.Add(assetPath);
            }
            catch (Exception ex)
            {
                failed.Add(new { path = assetPath, error = $"{ex.GetType().Name}: {ex.Message}" });
            }
        }

        Respond(new { type = "export_done", succeeded, failed, total = assetPaths.Count });
        return Task.CompletedTask;
    }

    // ─── Core export logic ────────────────────────────────────────────
    private static bool ExportAsset(string gamePath, string outputDir, Dictionary<string, bool> formats)
    {
        // Build nested output directory preserving the game path structure
        var assetDir = Path.GetDirectoryName(gamePath.Replace('/', Path.DirectorySeparatorChar)) ?? "";
        var nestedOutputDir = Path.Combine(outputDir, assetDir);
        Directory.CreateDirectory(nestedOutputDir);

        // For non-UE package files (.wem, .bnk, .bin, etc.), do raw binary export
        var lowerPath = gamePath.ToLowerInvariant();
        var isPackage = lowerPath.EndsWith(".uasset") || lowerPath.EndsWith(".umap") || lowerPath.EndsWith(".upk");
        if (!isPackage)
        {
            return ExportRawFile(gamePath, nestedOutputDir);
        }

        // Strip package extension for CUE4Parse — it uses extensionless game paths
        // NOTE: .upk is NOT stripped because FindGameFile only tries .uasset/.umap
        // — we registered .upk files with their full path so exact match works
        var objectPath = gamePath;
        foreach (var ext in new[] { ".uasset", ".umap" })
        {
            if (objectPath.EndsWith(ext, StringComparison.OrdinalIgnoreCase))
            {
                objectPath = objectPath[..^ext.Length];
                break;
            }
        }

        // Load the package and resolve all exports
        var exports = new List<UObject>();
        try
        {
            var pkg = _provider!.LoadPackage(objectPath);
            if (pkg != null)
            {
                foreach (var obj in pkg.GetExports())
                {
                    try { exports.Add(obj); }
                    catch { /* skip unresolvable */ }
                }
            }
        }
        catch when (lowerPath.EndsWith(".upk"))
        {
            // UE3 .upk packages may not be parseable by CUE4Parse — fall back to raw binary export
            return ExportRawFile(gamePath, nestedOutputDir);
        }

        if (exports.Count == 0 && lowerPath.EndsWith(".upk"))
        {
            // No exports found — fall back to raw copy
            return ExportRawFile(gamePath, nestedOutputDir);
        }

        bool exported = false;

        foreach (var obj in exports)
        {
            try
            {
                switch (obj)
                {
                    case UStaticMesh sm when formats.GetValueOrDefault("mesh", true):
                        exported |= ExportMesh(sm, nestedOutputDir);
                        break;

                    case USkeletalMesh sk when formats.GetValueOrDefault("mesh", true):
                        exported |= ExportMesh(sk, nestedOutputDir);
                        break;

                    case UTexture2D tex when formats.GetValueOrDefault("texture", true):
                        exported |= ExportTexture(tex, nestedOutputDir);
                        break;

                    case UAnimSequence anim when formats.GetValueOrDefault("animation", true):
                        exported |= ExportAnimation(anim, nestedOutputDir);
                        break;

                    case USoundWave sound when formats.GetValueOrDefault("audio", true):
                        exported |= ExportAudio(sound, nestedOutputDir);
                        break;

                    case UMaterialInstance mat when formats.GetValueOrDefault("props", true):
                        exported |= ExportProps(mat, nestedOutputDir);
                        break;
                }
            }
            catch (Exception ex)
            {
                Respond(new { type = "warning", message = $"{obj.Name} ({obj.ExportType}): {ex.Message}" });
            }

            // Export props for every object regardless of type
            if (formats.GetValueOrDefault("props", true))
            {
                exported |= ExportGenericProps(obj, nestedOutputDir);
            }
        }

        return exported;
    }

    // ─── Raw binary export (for .wem, .bnk, etc.) ────────────────────
    private static bool ExportRawFile(string gamePath, string nestedOutputDir)
    {
        byte[]? data = null;

        // Try CUE4Parse provider first
        if (_provider!.TrySaveAsset(gamePath, out var providerData))
        {
            data = providerData;
        }
        // Fall back to loose file on disk
        else if (_looseFiles.TryGetValue(gamePath, out var diskPath) && File.Exists(diskPath))
        {
            data = File.ReadAllBytes(diskPath);
        }

        if (data == null) return false;

        var name = Path.GetFileName(gamePath);
        var lowerName = name.ToLowerInvariant();

        // Convert .wem files to the target audio format via vgmstream
        if (lowerName.EndsWith(".wem"))
        {
            EnsureWemNameMap();

            // Rename using the debug name from the Wwise event hierarchy
            var wemId = Path.GetFileNameWithoutExtension(name);
            var baseName = _wemIdToName.TryGetValue(wemId, out var debugName)
                ? $"{debugName}_{wemId}"
                : wemId;

            var wemPath = Path.Combine(nestedOutputDir, $"{baseName}.wem");
            File.WriteAllBytes(wemPath, data);
            var converted = ConvertWithVgmstream(wemPath, nestedOutputDir, _audioFormat);
            if (converted)
            {
                try { File.Delete(wemPath); } catch { }
                return true;
            }
            // Keep the .wem as fallback if conversion failed
            return true;
        }

        // Try converting .ewem files — some are just renamed .wem (not actually encrypted)
        if (lowerName.EndsWith(".ewem"))
        {
            var ewemBase = Path.GetFileNameWithoutExtension(name);
            var wemPath = Path.Combine(nestedOutputDir, $"{ewemBase}.wem");
            File.WriteAllBytes(wemPath, data);
            var converted = ConvertWithVgmstream(wemPath, nestedOutputDir, _audioFormat);
            if (converted)
            {
                try { File.Delete(wemPath); } catch { }
                return true;
            }
            // Conversion failed — likely encrypted. Keep as .wem for manual handling.
            Respond(new { type = "warning", message = $"{name}: possibly encrypted audio, conversion failed" });
            return true;
        }

        var outPath = Path.Combine(nestedOutputDir, name);
        File.WriteAllBytes(outPath, data);
        return true;
    }

    // ─── Mesh export ──────────────────────────────────────────────────
    private static bool ExportMesh(UObject mesh, string outputDir)
    {
        var options = new ExporterOptions(); // ActorX → PSK/PSKX
        MeshExporter exporter;
        if (mesh is UStaticMesh staticMesh)
            exporter = new MeshExporter(staticMesh, options);
        else if (mesh is USkeletalMesh skelMesh)
            exporter = new MeshExporter(skelMesh, options);
        else
            return false;

        if (exporter.MeshLods.Count == 0)
            return false;

        bool any = false;
        for (int i = 0; i < exporter.MeshLods.Count; i++)
        {
            var lod = exporter.MeshLods[i];
            var ext = Path.GetExtension(lod.FileName);
            var name = exporter.MeshLods.Count > 1
                ? $"{mesh.Name}_LOD{i}{ext}"
                : $"{mesh.Name}{ext}";
            var outPath = Path.Combine(outputDir, name);
            File.WriteAllBytes(outPath, lod.FileData);
            any = true;
        }
        return any;
    }

    // ─── Texture export ───────────────────────────────────────────────
    private static bool ExportTexture(UTexture2D texture, string outputDir)
    {
        var decoded = texture.Decode();
        if (decoded == null) return false;

        if (_textureFormat == "tga")
        {
            // Export as raw BGRA TGA
            var outPath = Path.Combine(outputDir, $"{texture.Name}.tga");
            WriteTga(decoded, outPath);
        }
        else
        {
            var outPath = Path.Combine(outputDir, $"{texture.Name}.png");
            using var fs = File.Create(outPath);
            decoded.Encode(SkiaSharp.SKEncodedImageFormat.Png, 100).SaveTo(fs);
        }
        return true;
    }

    private static void WriteTga(SkiaSharp.SKBitmap bmp, string outPath)
    {
        int w = bmp.Width, h = bmp.Height;
        using var fs = File.Create(outPath);
        // TGA header (18 bytes)
        var header = new byte[18];
        header[2] = 2;  // Uncompressed true-color
        header[12] = (byte)(w & 0xFF);
        header[13] = (byte)((w >> 8) & 0xFF);
        header[14] = (byte)(h & 0xFF);
        header[15] = (byte)((h >> 8) & 0xFF);
        header[16] = 32; // bits per pixel
        header[17] = 0x28; // top-left origin + 8 alpha bits
        fs.Write(header);
        // Pixel data — BGRA
        for (int y = 0; y < h; y++)
        {
            for (int x = 0; x < w; x++)
            {
                var c = bmp.GetPixel(x, y);
                fs.WriteByte(c.Blue);
                fs.WriteByte(c.Green);
                fs.WriteByte(c.Red);
                fs.WriteByte(c.Alpha);
            }
        }
    }

    // ─── Animation export ─────────────────────────────────────────────
    private static bool ExportAnimation(UAnimSequence anim, string outputDir)
    {
        var options = new ExporterOptions();
        var exporter = new AnimExporter(anim, options);
        if (exporter.AnimSequences.Count == 0) return false;

        bool any = false;
        for (int i = 0; i < exporter.AnimSequences.Count; i++)
        {
            var seq = exporter.AnimSequences[i];
            var ext = Path.GetExtension(seq.FileName);
            var name = exporter.AnimSequences.Count > 1
                ? $"{anim.Name}_{i}{ext}"
                : $"{anim.Name}{ext}";
            var outPath = Path.Combine(outputDir, name);
            File.WriteAllBytes(outPath, seq.FileData);
            any = true;
        }
        return any;
    }

    // ─── Audio export ─────────────────────────────────────────────────
    private static bool ExportAudio(USoundWave sound, string outputDir)
    {
        sound.Decode(true, out var format, out var data);
        if (data == null || data.Length == 0)
            return false;

        var isNative = format switch
        {
            "OGG" or "OPUS" => true,
            "WAV" or "ADPCM" or "PCM" => true,
            _ => false
        };

        if (isNative)
        {
            // Already decoded — save in target format (CUE4Parse decoded to WAV/OGG)
            var nativeExt = format switch
            {
                "OGG" or "OPUS" => "ogg",
                _ => "wav"
            };
            var outPath = Path.Combine(outputDir, $"{sound.Name}.{nativeExt}");
            File.WriteAllBytes(outPath, data);
            return true;
        }

        // WEM / BINKA / unknown — save temp file and convert via vgmstream
        var tempExt = format switch
        {
            "WEM" => "wem",
            "BINKA" => "binka",
            _ => "bin"
        };
        var tempPath = Path.Combine(outputDir, $"{sound.Name}.{tempExt}");
        File.WriteAllBytes(tempPath, data);

        var converted = ConvertWithVgmstream(tempPath, outputDir, _audioFormat);
        if (converted)
        {
            try { File.Delete(tempPath); } catch { }
            return true;
        }
        // Keep the unconverted file as fallback
        return true;
    }

    // ─── Props export ─────────────────────────────────────────────────
    private static readonly Newtonsoft.Json.JsonSerializerSettings _propsSettings = new()
    {
        Formatting = Formatting.Indented,
        ReferenceLoopHandling = ReferenceLoopHandling.Ignore,
        MaxDepth = 64,
        Error = (_, args) => args.ErrorContext.Handled = true
    };

    /// <summary>Serialize a UObject to JSON, including all typed fields (not just .Properties).</summary>
    private static string SerializeUObject(UObject obj)
    {
        // Newtonsoft on the full UObject captures all deserialized C# fields
        // (.Properties alone is often empty for typed exports like UStaticMesh)
        var json = JsonConvert.SerializeObject(obj, _propsSettings);
        return json;
    }

    private static bool ExportProps(UMaterialInstance mat, string outputDir)
    {
        string? propsJson = null;
        var thread = new Thread(() =>
        {
            try { propsJson = SerializeUObject(mat); }
            catch { }
        }, 4 * 1024 * 1024);
        thread.IsBackground = true;
        thread.Start();
        thread.Join(15_000);

        if (propsJson == null) return false;

        var outPath = Path.Combine(outputDir, $"{mat.Name}.props.txt");
        File.WriteAllText(outPath, propsJson);
        return true;
    }

    private static bool ExportGenericProps(UObject obj, string outputDir)
    {
        try
        {
            // Use a bounded-stack thread to prevent StackOverflow on complex objects
            string? propsJson = null;
            var thread = new Thread(() =>
            {
                try { propsJson = SerializeUObject(obj); }
                catch { /* serialization failed — propsJson stays null */ }
            }, 4 * 1024 * 1024);
            thread.IsBackground = true;
            thread.Start();
            thread.Join(15_000);

            if (propsJson == null) return false;

            var outPath = Path.Combine(outputDir, $"{obj.Name}.props.txt");
            if (!File.Exists(outPath))
                File.WriteAllText(outPath, propsJson);
            return true;
        }
        catch (Exception ex)
        {
            Respond(new { type = "warning", message = $"Props export failed for {obj.Name}: {ex.Message}" });
            return false;
        }
    }

    // ─── Oodle Initialization ─────────────────────────────────────────
    private static readonly string[] _oodleNames = { "oo2core_9_win64.dll", "oodle-data-shared.dll" };

    private static void InitOodle(string gameDir)
    {
        // 1. Search for an existing Oodle DLL near the game, the app, or already cached
        var searchDirs = new List<string>
        {
            AppContext.BaseDirectory,
            Directory.GetCurrentDirectory(),
            Path.Combine(Path.GetTempPath(), "CUE4ParseCLI_oodle")
        };

        // Walk up from game paks dir to find the game's Binaries (e.g., …/Binaries/Win64/)
        try
        {
            var dir = new DirectoryInfo(gameDir);
            for (int i = 0; i < 5 && dir?.Parent != null; i++)
            {
                dir = dir.Parent;
                searchDirs.Add(dir.FullName);
                var bin64 = Path.Combine(dir.FullName, "Binaries", "Win64");
                if (Directory.Exists(bin64)) searchDirs.Add(bin64);
            }
        }
        catch { }

        foreach (var d in searchDirs)
        {
            foreach (var name in _oodleNames)
            {
                var candidate = Path.Combine(d, name);
                if (File.Exists(candidate))
                {
                    Respond(new { type = "info", message = $"Found Oodle: {candidate}" });
                    LoadOodle(candidate);
                    return;
                }
            }
        }

        // 2. Try CUE4Parse's built-in download
        var cacheDir = Path.Combine(Path.GetTempPath(), "CUE4ParseCLI_oodle");
        Directory.CreateDirectory(cacheDir);
        try
        {
            var tempDll = Path.Combine(cacheDir, OodleHelper.OODLE_DLL_NAME);
            OodleHelper.DownloadOodleDllAsync(tempDll).GetAwaiter().GetResult();
            if (File.Exists(tempDll) && new FileInfo(tempDll).Length > 0)
            {
                Respond(new { type = "info", message = "Oodle downloaded via CUE4Parse" });
                LoadOodle(tempDll);
                return;
            }
        }
        catch { }

        // 3. Manual download from OodleUE GitHub releases
        var targetPath = Path.Combine(cacheDir, "oodle-data-shared.dll");

        Respond(new { type = "info", message = "Downloading Oodle decompression library..." });
        try
        {
            using var http = new HttpClient();
            http.Timeout = TimeSpan.FromSeconds(30);
            var url = "https://github.com/WorkingRobot/OodleUE/releases/download/2026-01-25-1223/clang-cl-x64-release.zip";
            using var response = http.GetAsync(url, HttpCompletionOption.ResponseHeadersRead).GetAwaiter().GetResult();
            response.EnsureSuccessStatusCode();
            using var zipStream = response.Content.ReadAsStreamAsync().GetAwaiter().GetResult();
            using var zip = new ZipArchive(zipStream, ZipArchiveMode.Read);
            var entry = zip.GetEntry("bin/oodle-data-shared.dll")
                       ?? throw new FileNotFoundException("oodle-data-shared.dll not found in zip");
            using var entryStream = entry.Open();
            using var fs = File.Create(targetPath);
            entryStream.CopyTo(fs);
            Respond(new { type = "info", message = $"Downloaded Oodle ({new FileInfo(targetPath).Length} bytes)" });
            LoadOodle(targetPath);
        }
        catch (Exception ex)
        {
            Respond(new { type = "warning", message = $"Oodle download failed: {ex.Message}. IoStore archives won't decompress." });
        }
    }

    private static void LoadOodle(string path)
    {
        try
        {
            OodleHelper.Initialize(path);
            Respond(new { type = "info", message = "Oodle initialized" });
        }
        catch (Exception ex)
        {
            Respond(new { type = "warning", message = $"Oodle load failed: {ex.GetType().Name}: {ex.Message}" });
        }
    }

    // ─── vgmstream (WEM → WAV/OGG conversion) ────────────────────────

    private static void InitVgmstream()
    {
        if (_vgmstreamPath != null) return;

        var cacheDir = Path.Combine(Path.GetTempPath(), "CUE4ParseCLI_vgmstream");
        var exePath = Path.Combine(cacheDir, "vgmstream-cli.exe");

        if (File.Exists(exePath))
        {
            _vgmstreamPath = exePath;
            return;
        }

        Directory.CreateDirectory(cacheDir);
        Respond(new { type = "info", message = "Downloading vgmstream for audio conversion..." });

        try
        {
            using var http = new HttpClient();
            http.Timeout = TimeSpan.FromSeconds(60);
            http.DefaultRequestHeaders.UserAgent.ParseAdd("CUE4ParseCLI/1.0");
            var url = "https://github.com/vgmstream/vgmstream/releases/latest/download/vgmstream-win64.zip";
            using var resp = http.GetAsync(url, HttpCompletionOption.ResponseHeadersRead).GetAwaiter().GetResult();
            resp.EnsureSuccessStatusCode();
            using var zipStream = resp.Content.ReadAsStreamAsync().GetAwaiter().GetResult();
            using var zip = new ZipArchive(zipStream, ZipArchiveMode.Read);
            zip.ExtractToDirectory(cacheDir, overwriteFiles: true);

            if (File.Exists(exePath))
            {
                _vgmstreamPath = exePath;
                Respond(new { type = "info", message = "vgmstream downloaded and ready" });
            }
            else
            {
                Respond(new { type = "warning", message = "vgmstream zip extracted but vgmstream-cli.exe not found" });
            }
        }
        catch (Exception ex)
        {
            Respond(new { type = "warning", message = $"vgmstream download failed: {ex.Message}. WEM files won't be converted." });
        }
    }

    /// <summary>Convert an audio file to WAV (or OGG if vgmstream supports it) using vgmstream-cli.</summary>
    private static bool ConvertWithVgmstream(string inputPath, string outputDir, string targetFormat)
    {
        InitVgmstream();
        if (_vgmstreamPath == null) return false;

        var baseName = Path.GetFileNameWithoutExtension(inputPath);
        // vgmstream always outputs WAV
        var outPath = Path.Combine(outputDir, $"{baseName}.wav");

        try
        {
            var psi = new ProcessStartInfo
            {
                FileName = _vgmstreamPath,
                Arguments = $"-o \"{outPath}\" \"{inputPath}\"",
                UseShellExecute = false,
                CreateNoWindow = true,
                RedirectStandardOutput = true,
                RedirectStandardError = true
            };

            using var proc = Process.Start(psi);
            if (proc == null) return false;
            proc.WaitForExit(30_000);
            return proc.ExitCode == 0 && File.Exists(outPath);
        }
        catch (Exception ex)
        {
            Respond(new { type = "warning", message = $"vgmstream conversion failed for {Path.GetFileName(inputPath)}: {ex.Message}" });
            return false;
        }
    }

    // ─── Helpers ──────────────────────────────────────────────────────

    private static void HandleInspect(JObject cmd)
    {
        EnsureProvider();
        var path = cmd.Value<string>("path") ?? throw new ArgumentException("path required");

        // Strip standard UE4/5 extensions (but NOT .upk — registered with full path)
        foreach (var ext in new[] { ".uasset", ".umap", ".ubulk" })
        {
            if (path.EndsWith(ext, StringComparison.OrdinalIgnoreCase))
            {
                path = path[..^ext.Length];
                break;
            }
        }

        try
        {
            var pkg = _provider!.LoadPackage(path);
            var types = new List<object>();
            foreach (var obj in pkg.GetExports())
            {
                try { types.Add(new { name = obj.Name, type = obj.GetType().FullName }); }
                catch (Exception ex) { types.Add(new { name = "?", type = $"Error: {ex.Message}" }); }
            }
            Respond(new { type = "inspect_result", path, export_count = types.Count, exports = types });
        }
        catch (Exception ex)
        {
            RespondError($"Inspect failed for {path}: {ex.Message}");
        }
    }

    // ─── List Exports (enumerate exports inside a package) ────────────
    private static void HandleListExports(JObject cmd)
    {
        EnsureProvider();
        var rawPath = cmd.Value<string>("path") ?? throw new ArgumentException("path required");

        // Strip standard UE4/5 extensions (but NOT .upk — registered with full path)
        var path = rawPath;
        foreach (var ext in new[] { ".uasset", ".umap", ".ubulk" })
        {
            if (path.EndsWith(ext, StringComparison.OrdinalIgnoreCase))
            {
                path = path[..^ext.Length];
                break;
            }
        }

        // Use bounded-stack thread to prevent StackOverflowException on complex packages
        List<object>? exports = null;
        Exception? loadError = null;
        var thread = new Thread(() =>
        {
            try
            {
                var pkg = _provider!.LoadPackage(path);
                var result = new List<object>();
                foreach (var obj in pkg.GetExports())
                {
                    try
                    {
                        result.Add(new
                        {
                            name = obj.Name,
                            export_type = obj.ExportType ?? obj.GetType().Name,
                            outer = obj.Outer?.Name ?? ""
                        });
                    }
                    catch (Exception ex)
                    {
                        result.Add(new { name = "?", export_type = $"Error: {ex.Message}", outer = "" });
                    }
                }
                exports = result;
            }
            catch (Exception ex) { loadError = ex; }
        }, 4 * 1024 * 1024);
        thread.IsBackground = true;
        thread.Start();
        thread.Join(15_000); // 15 second timeout

        if (exports != null)
        {
            Respond(new { type = "exports_listed", path = rawPath, export_count = exports.Count, exports });
        }
        else
        {
            var errMsg = loadError?.Message ?? "Timed out loading package (too complex)";
            Respond(new
            {
                type = "exports_listed",
                path = rawPath,
                export_count = 0,
                exports = Array.Empty<object>(),
                error = errMsg
            });
        }
    }

    // ─── Get Props (full JSON content of a uasset) ────────────────────
    private static void HandleGetProps(JObject cmd)
    {
        EnsureProvider();
        var path = cmd.Value<string>("path") ?? throw new ArgumentException("path required");

        // Strip standard UE4/5 extensions (but NOT .upk — registered with full path)
        foreach (var ext in new[] { ".uasset", ".umap", ".ubulk" })
        {
            if (path.EndsWith(ext, StringComparison.OrdinalIgnoreCase))
            {
                path = path[..^ext.Length];
                break;
            }
        }

        try
        {
            var pkg = _provider!.LoadPackage(path);
            var exports = new List<object>();
            foreach (var obj in pkg.GetExports())
            {
                try
                {
                    // Use a bounded-stack thread (4 MB) to avoid StackOverflowException
                    // on deeply nested objects (e.g. WWise, complex Blueprints)
                    string? jsonStr = null;
                    Exception? serErr = null;
                    var thread = new Thread(() =>
                    {
                        try { jsonStr = SerializeUObject(obj); }
                        catch (Exception ex) { serErr = ex; }
                    }, 4 * 1024 * 1024);
                    thread.IsBackground = true;
                    thread.Start();
                    thread.Join(15_000); // 15 second timeout

                    if (jsonStr != null)
                    {
                        var jsonObj = JToken.Parse(jsonStr);
                        exports.Add(new
                        {
                            name = obj.Name,
                            export_type = obj.ExportType ?? obj.GetType().Name,
                            properties = jsonObj
                        });
                    }
                    else
                    {
                        var errMsg = serErr?.Message ?? "Serialization timed out (object too complex)";
                        exports.Add(new
                        {
                            name = obj.Name,
                            export_type = obj.ExportType ?? obj.GetType().Name,
                            properties = (object)new { error = errMsg }
                        });
                    }
                }
                catch (Exception ex)
                {
                    exports.Add(new
                    {
                        name = obj.Name,
                        export_type = "Error",
                        properties = (object)new { error = ex.Message }
                    });
                }
            }

            // Build the response manually to embed the pre-serialized JSON
            var result = new JObject
            {
                ["type"] = "props_result",
                ["path"] = path,
                ["export_count"] = exports.Count,
                ["exports"] = JToken.FromObject(exports, Newtonsoft.Json.JsonSerializer.Create(_propsSettings))
            };
            // Write directly to avoid double-serialization via System.Text.Json
            Console.Out.WriteLine(result.ToString(Formatting.None));
            Console.Out.Flush();
        }
        catch (Exception ex)
        {
            RespondError($"GetProps failed for {path}: {ex.Message}");
        }
    }

    // ─── WEM Name Mapping ─────────────────────────────────────────────

    /// <summary>
    /// Ensure the WEM ID → debug name map is populated.
    /// Checks a per-game disk cache first; builds from soundbank files on a miss.
    /// </summary>
    private static void EnsureWemNameMap()
    {
        if (_wemMapBuilt) return;
        _wemMapBuilt = true;

        // Try loading from the disk cache (survives across sessions for the same game)
        if (_wemNameCachePath != null && File.Exists(_wemNameCachePath))
        {
            try
            {
                var json = File.ReadAllText(_wemNameCachePath);
                var cached = System.Text.Json.JsonSerializer.Deserialize<Dictionary<string, string>>(json);
                if (cached != null && cached.Count > 0)
                {
                    _wemIdToName = cached;
                    Respond(new { type = "info", message = $"WEM names: loaded {_wemIdToName.Count} entries from cache" });
                    return;
                }
            }
            catch { /* ignore corrupt cache — fall through to rebuild */ }
        }

        BuildWemNameMapFromBanks();
    }

    // ─── Scan WWise AkAudioEvent assets ───────────────────────────────

    /// <summary>
    /// Scan all AkAudioEvent .uasset files under WwiseAudio/Events,
    /// extract media entries, and return organized audio data.
    /// Also supplements the WEM ID→name map.
    /// </summary>
    private static void HandleScanWwiseEvents(JObject cmd)
    {
        EnsureProvider();

        // Locate the WwiseAudio root and Events subfolder
        string? wwiseRoot = null;
        var eventAssets = new List<string>();

        foreach (var file in _provider!.Files)
        {
            var path = file.Value.Path;
            var lower = path.ToLowerInvariant();

            int idx = lower.IndexOf("wwiseaudio/");
            if (idx >= 0 && wwiseRoot == null)
                wwiseRoot = path[..(idx + "WwiseAudio/".Length)];

            // Collect .uasset files under Events subfolder
            if (idx >= 0 && lower.EndsWith(".uasset"))
            {
                var afterRoot = lower[(idx + "wwiseaudio/".Length)..];
                if (afterRoot.StartsWith("events/"))
                    eventAssets.Add(path);
            }
        }

        if (wwiseRoot == null || eventAssets.Count == 0)
        {
            Respond(new { type = "wwise_scan_result", found = false, wwise_root = "", audio = Array.Empty<object>() });
            return;
        }

        Respond(new
        {
            type = "progress",
            current = 0,
            total = eventAssets.Count,
            message = $"Scanning {eventAssets.Count} WWise events..."
        });

        var audioEntries = new List<object>();
        var seenMedia = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        var eventsPrefix = wwiseRoot + "Events/";
        int scanned = 0;

        foreach (var assetPath in eventAssets)
        {
            if (_cts.Token.IsCancellationRequested) break;
            scanned++;
            if (scanned % 50 == 0 || scanned == eventAssets.Count)
                Respond(new
                {
                    type = "progress",
                    current = scanned,
                    total = eventAssets.Count,
                    message = $"Scanning WWise events: {scanned}/{eventAssets.Count}"
                });

            try
            {
                var objectPath = assetPath[..^7]; // strip .uasset
                var pkg = _provider.LoadPackage(objectPath);
                if (pkg == null) continue;

                foreach (var obj in pkg.GetExports())
                {
                    if (obj.ExportType != "AkAudioEvent") continue;

                    // Serialize on a thread with a bounded stack to avoid StackOverflow
                    // from deeply recursive WWise object graphs
                    JObject? jObj = null;
                    Exception? serEx = null;
                    var thread = new Thread(() =>
                    {
                        try
                        {
                            var settings = new Newtonsoft.Json.JsonSerializerSettings
                            {
                                Formatting = Formatting.None,
                                ReferenceLoopHandling = ReferenceLoopHandling.Ignore,
                                MaxDepth = 48,
                                Error = (_, args) => args.ErrorContext.Handled = true
                            };
                            var json = JsonConvert.SerializeObject(obj, settings);
                            jObj = JObject.Parse(json);
                        }
                        catch (Exception ex) { serEx = ex; }
                    }, 4 * 1024 * 1024); // 4 MB stack
                    thread.IsBackground = true;
                    thread.Start();
                    thread.Join(10_000); // 10 second timeout

                    if (jObj == null) continue;

                    // Relative path of this event within Events/
                    var relPath = assetPath.StartsWith(eventsPrefix, StringComparison.OrdinalIgnoreCase)
                        ? assetPath[eventsPrefix.Length..]
                        : assetPath;
                    var eventDir = Path.GetDirectoryName(relPath)?.Replace('\\', '/') ?? "";

                    // Navigate the serialized JSON to extract media entries
                    // Path: EventCookedData → EventLanguageMap → [*].Value → Media → [*]
                    var langMap = jObj.SelectToken("EventCookedData.EventLanguageMap") as JArray;
                    if (langMap == null) continue;

                    foreach (var langEntry in langMap)
                    {
                        var mediaArray = langEntry.SelectToken("Value.Media") as JArray;
                        if (mediaArray == null) continue;

                        foreach (var mediaItem in mediaArray)
                        {
                            var debugName = mediaItem.Value<string>("DebugName") ?? "";
                            var mediaPath = mediaItem.Value<string>("MediaPathName") ?? "";
                            var mediaId = mediaItem.Value<long?>("MediaId") ?? 0;

                            if (string.IsNullOrEmpty(debugName) || string.IsNullOrEmpty(mediaPath)) continue;

                            // Deduplicate across events (same wem may be in multiple events)
                            if (!seenMedia.Add($"{eventDir}/{mediaPath}")) continue;

                            // Build full VFS path for the .wem
                            var fullWemPath = wwiseRoot + mediaPath;
                            var cleanName = Path.GetFileNameWithoutExtension(debugName);

                            audioEntries.Add(new
                            {
                                debug_name = cleanName,
                                media_id = mediaId,
                                wem_vfs_path = fullWemPath,
                                event_name = obj.Name,
                                event_folder = eventDir
                            });

                            _wemIdToName.TryAdd(mediaId.ToString(), cleanName);
                        }
                    }
                }
            }
            catch (Exception ex)
            {
                Respond(new { type = "warning", message = $"WWise scan: skipped {Path.GetFileName(assetPath)}: {ex.Message}" });
            }
        }

        Respond(new
        {
            type = "wwise_scan_result",
            found = true,
            wwise_root = wwiseRoot,
            events_prefix = eventsPrefix,
            total_events = eventAssets.Count,
            total_audio = audioEntries.Count,
            audio = audioEntries
        });
    }

    // ─── Export WWise audio with proper naming ────────────────────────

    /// <summary>
    /// Export .wem files from the VFS, rename to friendly names, and convert
    /// to the target audio format. Output is placed in the Events folder structure.
    /// </summary>
    private static Task HandleExportWwiseAudio(JObject cmd)
    {
        EnsureProvider();
        var outputDir = cmd.Value<string>("output_dir") ?? throw new ArgumentException("output_dir required");
        var audioFormat = cmd.Value<string>("audio_format") ?? "wav";
        var entries = cmd["entries"] as JArray ?? throw new ArgumentException("entries required");

        _audioFormat = audioFormat;
        Directory.CreateDirectory(outputDir);

        int total = entries.Count, current = 0;
        var succeeded = new List<string>();
        var failed = new List<object>();

        Respond(new { type = "progress", current = 0, total, message = $"Exporting {total} audio files..." });

        foreach (var entry in entries)
        {
            if (_cts.Token.IsCancellationRequested) break;
            current++;

            var wemPath = entry.Value<string>("wem_vfs_path") ?? "";
            var targetName = entry.Value<string>("target_name") ?? "";
            var targetFolder = entry.Value<string>("target_folder") ?? "";

            if (current % 10 == 0 || current == total)
                Respond(new { type = "progress", current, total, message = $"Exporting audio: {targetName}" });

            try
            {
                if (!_provider!.TrySaveAsset(wemPath, out var data))
                {
                    failed.Add(new { path = wemPath, error = "File not found in VFS" });
                    continue;
                }

                var outSubDir = Path.Combine(outputDir, targetFolder.Replace('/', Path.DirectorySeparatorChar));
                Directory.CreateDirectory(outSubDir);

                var wemTempPath = Path.Combine(outSubDir, $"{targetName}.wem");
                File.WriteAllBytes(wemTempPath, data);

                var converted = ConvertWithVgmstream(wemTempPath, outSubDir, audioFormat);
                if (converted)
                {
                    try { File.Delete(wemTempPath); } catch { }
                }
                // Keep .wem as fallback if conversion failed
                succeeded.Add(targetName);
            }
            catch (Exception ex)
            {
                failed.Add(new { path = wemPath, error = ex.Message });
            }
        }

        Respond(new { type = "export_done", succeeded, failed, total = entries.Count });
        return Task.CompletedTask;
    }

    /// <summary>Force-rebuild the WEM name map, deleting any stale disk cache.</summary>
    private static void HandleRebuildWemCache()
    {
        EnsureProvider();
        _wemIdToName = new(StringComparer.OrdinalIgnoreCase);
        _wemMapBuilt = false;
        if (_wemNameCachePath != null && File.Exists(_wemNameCachePath))
            try { File.Delete(_wemNameCachePath); } catch { }
        EnsureWemNameMap();
        Respond(new { type = "wem_cache_rebuilt", count = _wemIdToName.Count });
    }

    /// <summary>Scan all .bnk files in the provider and build the WEM ID → name map.</summary>
    private static void BuildWemNameMapFromBanks()
    {
        if (_provider == null) return;

        var bnkFiles = _provider.Files
            .Where(f => f.Value.Path.EndsWith(".bnk", StringComparison.OrdinalIgnoreCase))
            .Select(f => f.Value.Path)
            .ToList();

        if (bnkFiles.Count == 0)
        {
            Respond(new { type = "info", message = "WEM names: no soundbank files found in VFS" });
            return;
        }

        Respond(new { type = "info", message = $"WEM names: scanning {bnkFiles.Count} soundbank(s)..." });

        foreach (var bnkPath in bnkFiles)
        {
            try
            {
                if (!_provider.TrySaveAsset(bnkPath, out var bnkData)) continue;
                using var ar = new FByteArchive(bnkPath, bnkData);
                var wwise = new WwiseReader(ar);
                ProcessWwiseBank(wwise);
            }
            catch (Exception ex)
            {
                Respond(new { type = "warning", message = $"WEM map: skipped {Path.GetFileName(bnkPath)}: {ex.Message}" });
            }
        }

        Respond(new { type = "info", message = $"WEM names: built {_wemIdToName.Count} entries" });

        // Persist to disk cache for future sessions
        if (_wemNameCachePath != null)
        {
            try
            {
                Directory.CreateDirectory(Path.GetDirectoryName(_wemNameCachePath)!);
                File.WriteAllText(_wemNameCachePath,
                    System.Text.Json.JsonSerializer.Serialize(_wemIdToName));
            }
            catch { /* non-fatal if cache write fails */ }
        }
    }

    /// <summary>
    /// Walk a parsed WwiseReader's hierarchy tables and populate _wemIdToName.
    /// Maps SourceId (the .wem numeric filename) → the debug event name from BankStrMap.
    /// Note: CUE4Parse v1.2.2 exposes direct Event→Action→Sound links only;
    /// multi-level container children are not accessible in this API version.
    /// </summary>
    private static void ProcessWwiseBank(WwiseReader wwise)
    {
        var hierarchies = wwise.Hierarchies;
        var idToString = wwise.IdToString;
        if (hierarchies == null || idToString == null || idToString.Count == 0) return;

        // Build a flat ID → AbstractHierarchy lookup for this bank
        var byId = new Dictionary<uint, AbstractHierarchy>(hierarchies.Length);
        foreach (var h in hierarchies)
            if (h.Data != null)
                byId[h.Data.Id] = h.Data;

        // Each entry in IdToString is a named Wwise object (typically an event or sound)
        foreach (var (id, rawName) in idToString)
        {
            if (!byId.TryGetValue(id, out var hier)) continue;
            var name = SanitizeName(rawName);

            switch (hier)
            {
                case HierarchyEvent evt:
                    // Follow each EventAction → direct sound reference
                    foreach (var actionId in evt.EventActionIds)
                    {
                        if (!byId.TryGetValue(actionId, out var actionHier)) continue;
                        if (actionHier is not HierarchyEventAction action) continue;
                        // Direct sound: EventAction.ReferencedId is a SoundSfxVoice
                        if (byId.TryGetValue(action.ReferencedId, out var target) &&
                            target is HierarchySoundSfxVoice sound)
                        {
                            _wemIdToName.TryAdd(sound.SourceId.ToString(), name);
                        }
                    }
                    break;

                case HierarchySoundSfxVoice voice:
                    // Directly-named sound node — maps immediately
                    _wemIdToName.TryAdd(voice.SourceId.ToString(), name);
                    break;
            }
        }
    }

    /// <summary>Replace characters that are invalid in file names with underscores.</summary>
    private static string SanitizeName(string name)
    {
        var invalid = Path.GetInvalidFileNameChars();
        var sb = new System.Text.StringBuilder(name.Length);
        foreach (var c in name)
            sb.Append(Array.IndexOf(invalid, c) >= 0 || c is ' ' or '\\' or '/' ? '_' : c);
        return sb.ToString().Trim('_');
    }

    // ─── Helpers ──────────────────────────────────────────────────────

    private static void EnsureProvider()
    {
        if (_provider == null)
            throw new InvalidOperationException("Not initialized. Send 'init' command first.");
    }

    private static void Respond(object obj)
    {
        var json = System.Text.Json.JsonSerializer.Serialize(obj, _writeOpts);
        lock (_respondLock)
        {
            Console.Out.WriteLine(json);
            Console.Out.Flush();
        }
    }

    private static void RespondError(string message)
    {
        Respond(new { type = "error", message });
    }
}
