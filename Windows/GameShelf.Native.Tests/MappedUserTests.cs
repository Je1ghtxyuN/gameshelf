using System.Reflection;
using System.Text;
using GameShelf.Windows;
static class MappedUserTests
{
    static int calls,mode;
    static string expected="Z:";
    public static uint Fake(string drive,StringBuilder output,ref uint size)
    {
        calls++;if(drive!=expected)throw new Exception("Query changed the exact drive");
        if(mode==1){size=5000;return 234;}
        if(mode==2)return 2250;
        if(mode==3)return 0;
        if(mode==4){size=1024;return 234;}
        if(calls==1&&mode==5){size=1024;return 234;}
        output.Append("DOMAIN\\fixture");return 0;
    }
    public static Task Run()
    {
        var type=typeof(ShellMappingBackend).Assembly.GetType("GameShelf.Windows.MappedUserNative")??throw new Exception("Mapped-user helper missing");
        var method=type.GetMethod("QueryUser",BindingFlags.NonPublic|BindingFlags.Static)!;
        var query=Delegate.CreateDelegate(method.GetParameters()[1].ParameterType,typeof(MappedUserTests).GetMethod(nameof(Fake))!);
        object? Call(string drive)=>method.Invoke(null,[drive,query]);
        void Error(string drive,int which,int code,int maxCalls){mode=which;calls=0;try{Call(drive);throw new Exception("Expected mapped-user failure");}catch(TargetInvocationException e){if(!Equals(e.InnerException?.GetType().GetProperty("NativeErrorCode")?.GetValue(e.InnerException),(uint)code)||calls>maxCalls)throw new Exception("Wrong/beyond-bound native error");}}
        mode=0;calls=0;if(!Equals(Call("Z:"),"DOMAIN\\fixture")||calls!=1)throw new Exception("Exact mapped-user success failed");
        mode=5;calls=0;if(!Equals(Call("Z:"),"DOMAIN\\fixture")||calls!=2)throw new Exception("Bounded resize failed");
        Error("Z:",1,234,1);Error("Z:",2,2250,1);Error("Z:",3,13,1);Error("Z:",4,234,2);
        foreach(var drive in new[]{"","Z:\\","\\\\fixture.invalid\\share","C:\\path"}){calls=0;try{Call(drive);throw new Exception("Non-exact drive accepted");}catch(TargetInvocationException e)when(e.InnerException is ArgumentException){if(calls!=0)throw new Exception("Invalid drive reached native API");}}
        return Task.CompletedTask;
    }
}
