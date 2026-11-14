using System;
using System.Collections.Generic;
using System.Globalization;
using System.Runtime.CompilerServices;
using System.Runtime.InteropServices;
using Microsoft.Extensions.Primitives;
public static class Answer {
  [UnmanagedCallersOnly(EntryPoint="native_collections", CallConvs=new[] {typeof(CallConvCdecl)})]
  public static int Collections() => GC.CollectionCount(0);
  [UnmanagedCallersOnly(EntryPoint="native_answer", CallConvs=new[] {typeof(CallConvCdecl)})]
  public static int Get(int offset) {
    var values=new List<int>();
    foreach(var segment in new StringTokenizer("43:"+(offset*2).ToString(CultureInfo.InvariantCulture),new[]{':' }))
      values.Add(int.Parse(segment.Value!,CultureInfo.InvariantCulture));
    GC.Collect();
    return values[0]+values[1];
  }
}
