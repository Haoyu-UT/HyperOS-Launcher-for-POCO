"""Read what the build needs from an APK's binary AndroidManifest.xml.

Standard library only, so it runs without the Android SDK.
Usage: python tools/apkinfo.py apk/MiuiHome.apk
"""
import struct
import sys
import zipfile

# Attribute resource IDs, used when an obfuscated manifest strips the attribute name strings.
ATTR_IDS = {
    0x01010003: "name",
    0x0101000B: "sharedUserId",
    0x0101020C: "minSdkVersion",
    0x01010270: "targetSdkVersion",
    0x0101021B: "versionCode",
    0x0101021C: "versionName",
    0x0101028E: "required",
}


def _string_pool(data, off):
    _, hsize, _, count, _, flags, strings_start, _ = struct.unpack_from("<HHIIIIII", data, off)
    utf8 = flags & 0x100
    offsets = struct.unpack_from(f"<{count}I", data, off + hsize)
    base = off + strings_start
    out = []
    for o in offsets:
        p = base + o
        if utf8:
            p += 2 if data[p] & 0x80 else 1  # UTF-16 length, not needed
            n = data[p]
            if n & 0x80:
                n = ((n & 0x7F) << 8) | data[p + 1]
                p += 2
            else:
                p += 1
            out.append(data[p:p + n].decode("utf-8", "replace"))
        else:
            n = struct.unpack_from("<H", data, p)[0]
            p += 2
            if n & 0x8000:
                n = ((n & 0x7FFF) << 16) | struct.unpack_from("<H", data, p)[0]
                p += 2
            out.append(data[p:p + n * 2].decode("utf-16-le", "replace"))
    return out


def _value(strings, raw, vtype, vdata):
    if raw != 0xFFFFFFFF:
        return strings[raw]
    if vtype == 0x12:
        return vdata != 0
    if vtype == 0x03:
        return strings[vdata]
    return vdata


def parse_axml(data):
    """Yield ("start", tag, attrs) and ("end", tag, None) for each element."""
    strings, res_ids = [], ()
    off = struct.unpack_from("<H", data, 2)[0]  # skip the file header
    while off < len(data):
        ctype, hsize, csize = struct.unpack_from("<HHI", data, off)
        if ctype == 0x0001:
            strings = _string_pool(data, off)
        elif ctype == 0x0180:
            res_ids = struct.unpack_from(f"<{(csize - hsize) // 4}I", data, off + hsize)
        elif ctype == 0x0102:
            _, name, astart, asize, acount = struct.unpack_from("<IIHHH", data, off + hsize)
            attrs = {}
            p = off + hsize + astart
            for _ in range(acount):
                _, aname, raw, _, _, vtype, vdata = struct.unpack_from("<IIIHBBI", data, p)
                if aname < len(res_ids) and res_ids[aname] in ATTR_IDS:
                    key = ATTR_IDS[res_ids[aname]]
                else:
                    key = strings[aname]
                attrs[key] = _value(strings, raw, vtype, vdata)
                p += asize
            yield "start", strings[name], attrs
        elif ctype == 0x0103:
            name = struct.unpack_from("<I", data, off + hsize + 4)[0]
            yield "end", strings[name], None
        off += csize


def resolve_string(arsc, resid):
    """Return the first string value of resource `resid` in a resources.arsc, or None."""
    pkg_id, type_id, entry_id = resid >> 24, (resid >> 16) & 0xFF, resid & 0xFFFF
    strings = None
    off = struct.unpack_from("<H", arsc, 2)[0]
    while off < len(arsc):
        ctype, hsize, csize = struct.unpack_from("<HHI", arsc, off)
        if ctype == 0x0001 and strings is None:
            strings = _string_pool(arsc, off)
        elif ctype == 0x0200 and struct.unpack_from("<I", arsc, off + 8)[0] == pkg_id:
            sub = off + hsize
            while sub < off + csize:
                stype, shsize, ssize = struct.unpack_from("<HHI", arsc, sub)
                if stype == 0x0201 and arsc[sub + 8] == type_id:
                    value = _type_entry(arsc, sub, shsize, entry_id)
                    if value is not None and value[0] == 0x03:
                        return strings[value[1]]
                sub += ssize
        off += csize
    return None


def _type_entry(arsc, off, hsize, entry_id):
    """(dataType, data) of one entry in a ResTable_type chunk, or None if absent."""
    flags = arsc[off + 9]
    count, entries_start = struct.unpack_from("<II", arsc, off + 12)
    table = off + hsize
    if flags & 0x01:  # sparse: sorted (index, offset / 4) pairs
        for k in range(count):
            idx, rel = struct.unpack_from("<HH", arsc, table + k * 4)
            if idx == entry_id:
                break
        else:
            return None
        rel *= 4
    elif flags & 0x02:  # 16-bit offsets / 4
        if entry_id >= count:
            return None
        rel = struct.unpack_from("<H", arsc, table + entry_id * 2)[0]
        if rel == 0xFFFF:
            return None
        rel *= 4
    else:
        if entry_id >= count:
            return None
        rel = struct.unpack_from("<I", arsc, table + entry_id * 4)[0]
        if rel == 0xFFFFFFFF:
            return None
    entry = off + entries_start + rel
    size, eflags = struct.unpack_from("<HH", arsc, entry)
    if eflags & 0x08:  # compact entry: type in the high byte of flags, data follows the key
        return eflags >> 8, struct.unpack_from("<I", arsc, entry + 4)[0]
    if eflags & 0x01:  # complex (bag) entry, not a plain value
        return None
    _, _, dtype, data = struct.unpack_from("<HBBI", arsc, entry + size)
    return dtype, data


def _qualify(package, name):
    if name.startswith("."):
        return package + name
    return name if "." in name else f"{package}.{name}"


def read_apk(path):
    with zipfile.ZipFile(path) as z:
        manifest = z.read("AndroidManifest.xml")
        native_libs = [(i.filename, i.compress_type != zipfile.ZIP_STORED)
                       for i in z.infolist()
                       if i.filename.startswith("lib/") and i.filename.endswith(".so")]

    info = {
        "package": None, "versionName": None, "versionCode": None, "sharedUserId": None,
        "minSdk": None, "targetSdk": None, "permissions": [], "declared_permissions": [],
        "libraries": [], "activities": [], "home_activities": [], "native_libs": native_libs,
    }
    activity = None
    for event, tag, attrs in parse_axml(manifest):
        if event == "end":
            if tag in ("activity", "activity-alias"):
                activity = None
            continue
        name = attrs.get("name")
        if tag == "manifest":
            info["package"] = attrs.get("package")
            info["versionName"] = attrs.get("versionName")
            info["versionCode"] = attrs.get("versionCode")
            info["sharedUserId"] = attrs.get("sharedUserId")
        elif tag == "uses-sdk":
            info["minSdk"] = attrs.get("minSdkVersion")
            info["targetSdk"] = attrs.get("targetSdkVersion")
        elif tag in ("uses-permission", "uses-permission-sdk-23"):
            if name and name not in info["permissions"]:
                info["permissions"].append(name)
        elif tag == "permission" and name:
            info["declared_permissions"].append(name)
        elif tag in ("uses-library", "uses-native-library") and name:
            info["libraries"].append((name, attrs.get("required", True)))
        elif tag in ("activity", "activity-alias") and name:
            activity = _qualify(info["package"], name)
            info["activities"].append(activity)
        elif tag == "category" and name == "android.intent.category.HOME" and activity:
            info["home_activities"].append(activity)
    info["recents_activities"] = [a for a in info["activities"] if a.endswith(".RecentsActivity")]
    # Some manifests point sharedUserId at a string resource instead of a literal.
    # An empty value (as in HyperOS's MiuiHome) means no shared UID; Android ignores it.
    if isinstance(info["sharedUserId"], int):
        with zipfile.ZipFile(path) as z:
            resolved = resolve_string(z.read("resources.arsc"), info["sharedUserId"])
        info["sharedUserId"] = f"@0x{info['sharedUserId']:08x}" if resolved is None else resolved or None
    return info


def signer_cert_sha256(path):
    """SHA-256 of the first signer's certificate from the APK Signing Block (v3, else v2), or None.

    This only identifies the signer; it doesn't verify the signature (apksigner does that).
    """
    import hashlib
    with open(path, "rb") as f:
        data = f.read()
    eocd = data.rfind(b"PK\x05\x06")
    if eocd < 0:
        return None
    cd_offset = struct.unpack_from("<I", data, eocd + 16)[0]
    if data[cd_offset - 16:cd_offset] != b"APK Sig Block 42":
        return None
    size = struct.unpack_from("<Q", data, cd_offset - 24)[0]
    pos, end = cd_offset - size, cd_offset - 24  # pairs follow the leading size field
    blocks = {}
    while pos < end:
        length, block_id = struct.unpack_from("<QI", data, pos)
        blocks[block_id] = data[pos + 12:pos + 8 + length]
        pos += 8 + length
    value = blocks.get(0xF05368C0) or blocks.get(0x7109871A)  # v3, v2
    if value is None:
        return None

    def lp(buf, at):  # length-prefixed slice -> (bytes, next offset)
        n = struct.unpack_from("<I", buf, at)[0]
        return buf[at + 4:at + 4 + n], at + 4 + n

    signers, _ = lp(value, 0)
    signer, _ = lp(signers, 0)
    signed_data, _ = lp(signer, 0)
    _, at = lp(signed_data, 0)  # digests
    certs, _ = lp(signed_data, at)
    cert, _ = lp(certs, 0)
    return hashlib.sha256(cert).hexdigest()


def privapp_xml(info):
    """Whitelist every permission the app requests but does not define itself.

    Listing non-privileged ones is harmless; missing a privileged one stops an enforcing ROM from booting.
    """
    own = set(info["declared_permissions"])
    lines = [
        '<?xml version="1.0" encoding="utf-8"?>',
        f'<!-- Generated by build.py from {info["package"]} {info["versionName"]} -->',
        "<permissions>",
        f'    <privapp-permissions package="{info["package"]}">',
    ]
    lines += [f'        <permission name="{p}"/>' for p in sorted(set(info["permissions"]) - own)]
    lines += ["    </privapp-permissions>", "</permissions>", ""]
    return "\n".join(lines)


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    info = read_apk(sys.argv[1])
    print(f"package:       {info['package']}  {info['versionName']} ({info['versionCode']})")
    print(f"sdk:           min {info['minSdk']}, target {info['targetSdk']}")
    print(f"sharedUserId:  {info['sharedUserId'] or '-'}")
    print(f"signer sha256: {signer_cert_sha256(sys.argv[1]) or 'no v2/v3 signature'}")
    print(f"home:          {', '.join(info['home_activities']) or '-'}")
    print(f"recents:       {', '.join(info['recents_activities']) or '-'}")
    print(f"permissions:   {len(info['permissions'])} requested, {len(info['declared_permissions'])} self-declared")
    libs = ", ".join(f"{n}{'' if r else ' (optional)'}" for n, r in info["libraries"])
    print(f"libraries:     {libs or '-'}")
    arm64 = [(f, c) for f, c in info["native_libs"] if f.startswith("lib/arm64-v8a/")]
    print(f"arm64 libs:    {len(arm64)} ({sum(c for _, c in arm64)} compressed)")


if __name__ == "__main__":
    main()
