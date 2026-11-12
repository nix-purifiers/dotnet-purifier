// SPDX-License-Identifier: MIT
using System;
using System.Collections.Generic;
using System.Collections.Immutable;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Reflection.Metadata;
using System.Reflection.PortableExecutable;
using System.Security.Cryptography;
using System.Text.Json;

sealed class Types : ISignatureTypeProvider<string, object>, ICustomAttributeTypeProvider<string> {
    public string GetPrimitiveType(PrimitiveTypeCode type) => type.ToString();
    public string GetSystemType() => "System.Type";
    public bool IsSystemType(string type) => type == GetSystemType();
    public string GetTypeFromSerializedName(string name) => name;
    public PrimitiveTypeCode GetUnderlyingEnumType(string type) => throw new InvalidDataException("Unexpected enum in native export attribute: " + type);
    public string GetTypeFromDefinition(MetadataReader r, TypeDefinitionHandle h, byte kind) {
        var t=r.GetTypeDefinition(h); var parent=t.GetDeclaringType();
        if(!parent.IsNil) return GetTypeFromDefinition(r,parent,kind)+"+"+r.GetString(t.Name);
        var ns=r.GetString(t.Namespace); return (ns.Length==0?"":ns+".")+r.GetString(t.Name);
    }
    public string GetTypeFromReference(MetadataReader r, TypeReferenceHandle h, byte kind) {
        var t=r.GetTypeReference(h);
        if(t.ResolutionScope.Kind==HandleKind.TypeReference) return GetTypeFromReference(r,(TypeReferenceHandle)t.ResolutionScope,kind)+"+"+r.GetString(t.Name);
        var ns=r.GetString(t.Namespace); return (ns.Length==0?"":ns+".")+r.GetString(t.Name);
    }
    public string GetTypeFromSpecification(MetadataReader r, object context, TypeSpecificationHandle h, byte kind) => r.GetTypeSpecification(h).DecodeSignature(this,context);
    public string GetArrayType(string type, ArrayShape shape) => type+"[rank="+shape.Rank+"]";
    public string GetSZArrayType(string type) => type+"[]";
    public string GetByReferenceType(string type) => type+"&";
    public string GetPointerType(string type) => type+"*";
    public string GetFunctionPointerType(MethodSignature<string> signature) => "fn("+string.Join(",",signature.ParameterTypes)+")->"+signature.ReturnType;
    public string GetGenericInstantiation(string type, ImmutableArray<string> args) => type+"<"+string.Join(",",args)+">";
    public string GetGenericMethodParameter(object context, int index) => "!!"+index;
    public string GetGenericTypeParameter(object context, int index) => "!"+index;
    public string GetModifiedType(string modifier,string type,bool required) => type+(required?" modreq(":" modopt(")+modifier+")";
    public string GetPinnedType(string type) => type+" pinned";
}

static class InspectExports {
    public static void Main(string[] args) {
        if(args.Length!=2) throw new ArgumentException("Expected managed assembly and output JSON paths");
        var bytes=File.ReadAllBytes(args[0]);
        using var pe=new PEReader(new MemoryStream(bytes));
        if(!pe.HasMetadata) throw new InvalidDataException("Native export ownership requires a managed PE assembly");
        var reader=pe.GetMetadataReader();
        var types=new Types();
        var exports=new List<object>();
        var names=new HashSet<string>(StringComparer.Ordinal);
        foreach(var handle in reader.MethodDefinitions) {
            var method=reader.GetMethodDefinition(handle);
            foreach(var attributeHandle in method.GetCustomAttributes()) {
                var attribute=reader.GetCustomAttribute(attributeHandle);
                EntityHandle owner;
                if(attribute.Constructor.Kind==HandleKind.MemberReference)
                    owner=reader.GetMemberReference((MemberReferenceHandle)attribute.Constructor).Parent;
                else if(attribute.Constructor.Kind==HandleKind.MethodDefinition)
                    owner=reader.GetMethodDefinition((MethodDefinitionHandle)attribute.Constructor).GetDeclaringType();
                else continue;
                string type;
                string scope="project";
                if(owner.Kind==HandleKind.TypeReference) {
                    var reference=reader.GetTypeReference((TypeReferenceHandle)owner);
                    type=types.GetTypeFromReference(reader,(TypeReferenceHandle)owner,0);
                    if(reference.ResolutionScope.Kind==HandleKind.AssemblyReference)
                        scope=reader.GetString(reader.GetAssemblyReference((AssemblyReferenceHandle)reference.ResolutionScope).Name);
                } else if(owner.Kind==HandleKind.TypeDefinition)
                    type=types.GetTypeFromDefinition(reader,(TypeDefinitionHandle)owner,0);
                else continue;
                if(type!="System.Runtime.InteropServices.UnmanagedCallersOnlyAttribute") continue;
                var value=attribute.DecodeValue(types);
                string entry=null;
                var conventions=new List<string>();
                foreach(var argument in value.NamedArguments) {
                    if(argument.Name=="EntryPoint") entry=argument.Value as string;
                    if(argument.Name=="CallConvs" && argument.Value is ImmutableArray<CustomAttributeTypedArgument<string>> convs)
                        conventions.AddRange(convs.Select(c=>c.Value?.ToString() ?? ""));
                }
                if(string.IsNullOrEmpty(entry)) continue;
                if((method.Attributes & MethodAttributes.Static)==0 || method.GetGenericParameters().Count!=0)
                    throw new InvalidDataException("Native exported method must be static and non-generic: "+entry);
                if(!names.Add(entry)) throw new InvalidDataException("Duplicate native entrypoint: "+entry);
                var signature=method.DecodeSignature(types,(object)null);
                exports.Add(new {entryPoint=entry,declaringType=types.GetTypeFromDefinition(reader,method.GetDeclaringType(),0),
                    method=reader.GetString(method.Name),attributeScope=scope,returnType=signature.ReturnType,
                    parameters=signature.ParameterTypes,callingConventions=conventions});
            }
        }
        var result=new {assembly=reader.GetString(reader.GetAssemblyDefinition().Name),
            mvid=reader.GetGuid(reader.GetModuleDefinition().Mvid).ToString(),
            sha256=Convert.ToHexString(SHA256.HashData(bytes)).ToLowerInvariant(),exports};
        File.WriteAllText(args[1],JsonSerializer.Serialize(result,new JsonSerializerOptions{WriteIndented=true})+"\n");
    }
}
