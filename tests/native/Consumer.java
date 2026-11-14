import java.lang.foreign.*;
import java.nio.file.Path;
public final class Consumer {
 public static void main(String[] args) throws Throwable {
  System.loadLibrary("answer");
  var library=SymbolLookup.loaderLookup();
  var counter=Linker.nativeLinker().downcallHandle(library.find("native_collections").orElseThrow(),FunctionDescriptor.of(ValueLayout.JAVA_INT));
  int before=(int)counter.invokeExact();
  var symbol=library.find("native_answer").orElseThrow();
  try (var arena=Arena.ofConfined()) {
    var info=arena.allocate(4*ValueLayout.ADDRESS.byteSize(),ValueLayout.ADDRESS.byteAlignment());
    var dladdr=Linker.nativeLinker().downcallHandle(Linker.nativeLinker().defaultLookup().find("dladdr").orElseThrow(),FunctionDescriptor.of(ValueLayout.JAVA_INT,ValueLayout.ADDRESS,ValueLayout.ADDRESS));
    int found=(int)dladdr.invokeExact(symbol,info);
    if(found==0)throw new AssertionError("native origin unavailable");
    var origin=info.get(ValueLayout.ADDRESS,0).reinterpret(Long.MAX_VALUE).getUtf8String(0);
    if(!Path.of(origin).toRealPath().equals(Path.of(args[0]).toRealPath()))throw new AssertionError(origin);
  }
  var function=Linker.nativeLinker().downcallHandle(symbol,FunctionDescriptor.of(ValueLayout.JAVA_INT,ValueLayout.JAVA_INT));
  int a=(int)function.invokeExact(0),b=(int)function.invokeExact(1);
  if(a!=Integer.parseInt(args[1]) || b!=Integer.parseInt(args[1])+2)throw new AssertionError(a+" "+b);
  int after=(int)counter.invokeExact();
  if(after-before<2)throw new AssertionError("managed GC did not execute");
  System.out.println(a+" "+b);
 }
}
